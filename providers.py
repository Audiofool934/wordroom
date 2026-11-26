"""Model provider settings and one JSON request helper, used by the word list scripts.

Set a provider and key with: python3 providers.py --data-dir <dir>
The key is stored in <dir>/providers.private.json with owner-only permissions.
Environment variables DEEPSEEK_API_KEY, DASHSCOPE_API_KEY and WORDROOM_API_KEY also work.
"""
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from http.client import HTTPException
import json
import os
import random
import threading
import time

from domain import AppError, text

PRESETS = {
    "deepseek": {"label":"DeepSeek", "base_url":"https://api.deepseek.com", "model":"deepseek-flash", "json_mode":True},
    "qwen": {"label":"Qwen (Alibaba Model Studio)", "base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1", "model":"qwen-plus", "json_mode":True},
    "custom": {"label":"Other OpenAI-compatible API", "base_url":"", "model":"", "json_mode":True},
}

class ProviderSettings:
    def __init__(self,directory):
        self.path=Path(directory)/"providers.private.json"
        self.lock=threading.RLock()
        self.data={"active":"deepseek","providers":{k:{**v,"api_key":""} for k,v in PRESETS.items()}}
        if self.path.exists():
            loaded=json.loads(self.path.read_text())
            self.data["active"]=loaded.get("active","deepseek")
            for key,value in loaded.get("providers",{}).items():
                if key in PRESETS:
                    self.data["providers"][key].update(value)
        for key,env in (("deepseek","DEEPSEEK_API_KEY"),("qwen","DASHSCOPE_API_KEY"),("custom","WORDROOM_API_KEY")):
            if not self.data["providers"][key]["api_key"]:
                self.data["providers"][key]["api_key"]=os.environ.get(env,"")
        if os.environ.get("WORDROOM_BASE_URL"):
            self.data["providers"]["custom"]["base_url"]=os.environ["WORDROOM_BASE_URL"]
        if os.environ.get("WORDROOM_MODEL"):
            self.data["providers"]["custom"]["model"]=os.environ["WORDROOM_MODEL"]

    def public(self):
        with self.lock:
            return {"active":self.data["active"],"providers":{
                k:{**{field:v[field] for field in ("label","base_url","model","json_mode")},"has_key":bool(v["api_key"])} for k,v in self.data["providers"].items()}}

    def active(self):
        with self.lock:
            name=self.data["active"]
            config=dict(self.data["providers"][name])
        if not config["api_key"]:
            raise AppError("No API key is set for this provider. Run: python3 providers.py --data-dir <dir>",400)
        self.validate_url(config["base_url"])
        text(config["model"],"Model name",160)
        return name,config

    @staticmethod
    def validate_url(value):
        text(value,"API base URL",500)
        try:
            url=urlsplit(value)
            port=url.port
        except ValueError:
            raise AppError("The API base URL is not valid.")
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise AppError("The API base URL must not contain credentials or a query.")
        if url.scheme!="https" and not (url.scheme=="http" and url.hostname in ("127.0.0.1","localhost","::1")):
            raise AppError("A remote API must use HTTPS; HTTP is allowed only on this machine.")
        if url.path.rstrip("/").endswith("/chat/completions"):
            raise AppError("Give the base URL, without /chat/completions.")

    def save(self,payload):
        name=payload.get("provider")
        if name not in PRESETS:
            raise AppError("Unknown provider.")
        base=text(payload.get("base_url"),"API base URL",500).rstrip("/")
        self.validate_url(base)
        model=text(payload.get("model"),"Model name",160)
        key=text(payload.get("api_key",""),"API key",2000,True)
        if any(c.isspace() for c in key):
            raise AppError("The API key must not contain spaces or line breaks.")
        json_mode=payload.get("json_mode",True)
        if not isinstance(json_mode,bool):
            raise AppError("Invalid JSON mode setting.")
        with self.lock:
            old=self.data["providers"][name]
            # Never send an old service's saved key to a newly configured address.
            saved_key=key or (old["api_key"] if old["base_url"]==base else "")
            if payload.get("clear_key") is True:
                saved_key=""
            new={**old,"base_url":base,"model":model,"api_key":saved_key,"json_mode":json_mode}
            updated={"active":name,"providers":{**self.data["providers"],name:new}}
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            temp=self.path.with_suffix(".tmp")
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,"w") as f:
                json.dump(updated,f,ensure_ascii=False,indent=2)
            os.chmod(temp,0o600)
            os.replace(temp,self.path)
            self.data=updated
        return self.public()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


def request_json(provider, config, system, task):
    payload={"model":config["model"],"stream":False,"max_tokens":4000,
             "messages":[{"role":"system","content":system},
                         {"role":"user","content":json.dumps(task,ensure_ascii=False)}]}
    if config["json_mode"]:
        payload["response_format"]={"type":"json_object"}
    if provider=="deepseek":
        if config["model"] in ("deepseek-flash","deepseek-v4-pro"):
            payload.update(thinking={"type":"enabled"},reasoning_effort="high",max_tokens=393216)
        else:
            payload["thinking"]={"type":"disabled"}
    elif provider=="qwen":
        payload["enable_thinking"]=False
    request=Request(config["base_url"].rstrip("/")+"/chat/completions",data=json.dumps(payload).encode(),
                    headers={"Authorization":"Bearer "+config["api_key"],"Content-Type":"application/json","User-Agent":"Wordroom/0.1"},method="POST")
    for attempt in range(4):
        try:
            with build_opener(NoRedirect()).open(request,timeout=600) as response:
                body=response.read(1_000_001)
            if len(body)>1_000_000:
                raise AppError("The model response is too large.",502)
            data=json.loads(body)
            choice=data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise AppError("The model output hit its length limit.",502)
            content=choice["message"]["content"]
            if not isinstance(content,str) or not content.strip():
                raise AppError("The model returned no content.",502)
            clean=content.strip()
            if clean.startswith("```"):
                clean=clean.split("\n",1)[1].rsplit("```",1)[0].strip()
            parsed=json.loads(clean)
            usage=data.get("usage") or {}
            safe_usage={k:max(0,int(usage.get(k,0) or 0)) for k in ("prompt_tokens","completion_tokens","total_tokens")}
            return parsed,safe_usage
        except HTTPError as exc:
            if exc.code in (429,500,502,503,504) and attempt<3:
                exc.close()
                time.sleep(2**attempt+random.random())
                continue
            messages={401:"The API key is invalid or expired.",403:"This key may not use this service or model.",404:"The API base URL or model name does not exist.",429:"Rate limited or out of quota; try again later.",400:"The model rejected the request; check the model name or turn off JSON mode."}
            message=messages.get(exc.code,f"The model service returned HTTP {exc.code}.")
            exc.close()
            raise AppError(message,502) from None
        except (HTTPException,OSError) as exc:
            if attempt<3:
                time.sleep(2**attempt+random.random())
                continue
            raise AppError(f"Could not reach the model service ({type(exc).__name__}).",502) from None
        except (ValueError,KeyError,IndexError,TypeError):
            raise AppError("The model did not return valid JSON.",502) from None


def main():
    import argparse
    import getpass
    parser = argparse.ArgumentParser(description="Set the model provider and API key for the word list scripts")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--provider", choices=sorted(PRESETS), default="deepseek")
    parser.add_argument("--base-url", help="Defaults to the provider's preset")
    parser.add_argument("--model", help="Defaults to the provider's preset")
    args = parser.parse_args()
    preset = PRESETS[args.provider]
    key = getpass.getpass(f"{preset['label']} API key (input hidden): ")
    settings = ProviderSettings(args.data_dir)
    try:
        settings.save({"provider": args.provider, "base_url": args.base_url or preset["base_url"],
                       "model": args.model or preset["model"], "api_key": key})
    except AppError as exc:
        raise SystemExit(str(exc))
    print(f"Saved {args.provider} settings to {settings.path}")


if __name__ == "__main__":
    main()
