"""Group the words of the word database into families that share a root.

Run with: uv run --python 3.12 --with nltk==3.9.1 scripts/build_word_families.py <words.sqlite3> data/wordlist_families.json

The Snowball stemmer does most of the work. The lists below hold the reviewed
corrections: stems that join unrelated words, families the stemmer misses, and
negated forms (un-, in-, ...) that belong with their base word.
"""
import argparse
from contextlib import closing
import json
from os.path import commonprefix
from pathlib import Path
import sqlite3

from nltk.stem import SnowballStemmer

PREPOSITIONS = {"to", "from", "of", "in", "at", "with", "by", "on", "for"}

# Each set leaves its stem family and becomes a family of its own.
SPLIT = [
    {"accession"}, {"affectation"}, {"competent"}, {"complicity"}, {"comprehensible"},
    {"conservation"}, {"contention"}, {"definitive"}, {"discrete"}, {"discretion"},
    {"duplicate"}, {"exactness"}, {"facilities"}, {"identity"}, {"impasse"}, {"impassioned"},
    {"impassive", "impassivity"}, {"integrity"}, {"intelligibility", "intelligible", "intelligibly"},
    {"liberal"}, {"negativity"}, {"negligible"}, {"numerical"}, {"officious"}, {"opportunism"},
    {"optimal"}, {"polite"}, {"politic"}, {"politically"}, {"positive"}, {"predate"}, {"revel"},
    {"sophist"}, {"substantiate", "substantiation"}, {"temperate"}, {"transit"}, {"captive"},
    {"diminutive"},
]

# Each tuple is one family the stemmer splits apart.
JOIN = [
    ("abhor", "abhorrent"), ("absence", "absent"), ("abstract", "abstraction"),
    ("acclaim", "acclamation"), ("admonish", "admonitory"), ("adulate", "adulatory"),
    ("altruism", "altruistic"), ("ambition", "ambitious"), ("anachronism", "anachronistic"),
    ("ancestral", "ancestry"), ("anomalous", "anomaly"), ("antagonism", "antagonistic"),
    ("antipathetic", "antipathy"), ("antithesis", "antithetical"), ("apathetic", "apathy"),
    ("apprehend", "apprehension"), ("approbation", "approbatory"), ("arbitrarily", "arbitrary"),
    ("asymmetrically", "asymmetry"), ("autonomous", "autonomy"), ("axiom", "axiomatic"),
    ("bewilder", "bewilderment"), ("blame", "blameless"), ("buoyancy", "buoyant"),
    ("burden", "burdensome"), ("bureaucracy", "bureaucratic"), ("caution", "cautious"),
    ("chauvinism", "chauvinistic"), ("chimera", "chimerical"), ("clarify", "clarity"),
    ("coincide", "coincidental"), ("collude", "collusion"), ("compassion", "compassionate"),
    ("compensate", "compensatory"), ("compete", "competition"), ("complement", "complementary"),
    ("compliment", "complimentary"), ("comply", "compliant"), ("comprehend", "comprehensible"),
    ("concede", "concession"), ("conform", "conformist"), ("congruent", "congruity"),
    ("consequence", "consequential"), ("conservatism", "conservative"), ("constrain", "constraint"),
    ("contagion", "contagious"), ("contemporaneity", "contemporary"), ("contempt", "contemptuous"),
    ("contend", "contention"), ("context", "contextual"), ("contradict", "contradictory"),
    ("controversy", "controverting"), ("cowardice", "cowardly"), ("critic", "critique"),
    ("curiosity", "curious"), ("decipher", "decipherable"), ("deduce", "deduction"),
    ("defend", "defense"), ("deference", "deferential"), ("degeneracy", "degenerative"),
    ("deliberate", "deliberative"), ("deprecate", "deprecatory"), ("destined", "destiny"),
    ("diagnose", "diagnosis"), ("dichotomous", "dichotomy"), ("didactic", "didacticism"),
    ("diminish", "diminution"), ("diplomacy", "diplomatic"), ("disclose", "disclosure"),
    ("discreet", "discretion", "discretionary"), ("discriminate", "discriminatory"),
    ("dispassion", "dispassionate"), ("disposed", "disposition"), ("disputatious", "dispute"),
    ("dissension", "dissenter"), ("dogma", "dogmatic"), ("duplicitous", "duplicity"),
    ("eclectic", "eclecticism"), ("economical", "economy"), ("egotism", "egotistical"),
    ("elitism", "elitist"), ("empirical", "empiricism", "empiricist"), ("enigma", "enigmatic"),
    ("enthusiasm", "enthusiast"), ("ephemera", "ephemeral"), ("evenhanded", "evenhandedness"),
    ("evident", "evidentiary"), ("exactitude", "exactness"), ("exculpate", "exculpatory"),
    ("exemplar", "exemplary", "exemplify"), ("expand", "expansion", "expansionist"),
    ("expedite", "expeditious"), ("expose", "exposure"), ("extend", "extension"),
    ("fanatical", "fanaticism"), ("fathom", "fathomless"), ("felicitous", "felicity"),
    ("fervency", "fervent"), ("finance", "financial"), ("flippancy", "flippant"),
    ("fluid", "fluidity"), ("foresee", "foreseen"), ("fragment", "fragmentary"),
    ("fraud", "fraudulence"), ("grand", "grandeur"), ("guile", "guileless"),
    ("hackneyed", "hackneyedness"), ("hedonism", "hedonist"), ("hierarchical", "hierarchy"),
    ("honor", "honorific"), ("hubris", "hubristic"), ("hypocrisy", "hypocritic"),
    ("idealism", "idealistic"), ("idiosyncrasy", "idiosyncratic"),
    ("illusion", "illusionary", "illusory"), ("imaginary", "imaginative"),
    ("immediacy", "immediate"), ("impede", "impediment"), ("inadequacy", "inadequate"),
    ("indeterminacy", "indeterminate", "indeterministic"), ("indiscreet", "indiscretion"),
    ("individualistic", "individuality"), ("inept", "ineptitude"), ("inert", "inertia", "inertial"),
    ("inhibit", "inhibitor", "inhibitory"), ("intense", "intensify", "intensification"),
    ("intricacy", "intricate"), ("inversion", "invert"), ("isolation", "isolationism"),
    ("laudable", "laudatory"), ("legitimacy", "legitimate"), ("leniency", "lenient"),
    ("lethargic", "lethargy"), ("loyal", "loyalty"), ("malice", "malicious"),
    ("master", "mastery"), ("melancholic", "melancholy"), ("mendacious", "mendacity"),
    ("merit", "meritorious"), ("mimic", "mimicry"), ("minute", "minutiae"),
    ("misapprehend", "misapprehension"), ("misrepresent", "misrepresentation"),
    ("modest", "modesty"), ("monotonous", "monotony"), ("moralistic", "moralize"),
    ("mordancy", "mordant"), ("nepotism", "nepotistic"), ("normalcy", "normalize"),
    ("nostalgia", "nostalgic"), ("notoriety", "notorious"), ("novel", "novelty"),
    ("nullify", "nullity"), ("obduracy", "obdurate"), ("obfuscate", "obfuscatory"),
    ("obsolescence", "obsolete"), ("obstinacy", "obstinate"), ("opportunism", "opportunistic"),
    ("opprobrious", "opprobrium"), ("optimism", "optimistic"), ("orthodox", "orthodoxy"),
    ("ostentation", "ostentatiousness"), ("pacific", "pacify"), ("paradigm", "paradigmatic"),
    ("partisan", "partisanship"), ("pedant", "pedantry"), ("peripheral", "periphery"),
    ("permeable", "permeate"), ("persuade", "persuasion"), ("pessimism", "pessimistic"),
    ("petrification", "petrify"), ("philosophical", "philosophy"), ("pilfer", "pilferage"),
    ("placate", "placatory"), ("platitude", "platitudinous"), ("portend", "portent"),
    ("pragmatic", "pragmatist"), ("precocious", "precocity"), ("prejudice", "prejudicial"),
    ("preoccupation", "preoccupied"), ("prescience", "prescient"), ("prescribe", "prescriptive"),
    ("preempt", "preemptive"), ("prestige", "prestigious"), ("presume", "presumption", "presumptuousness"),
    ("pretend", "pretense", "pretension", "pretentious"), ("prevail", "prevalence"),
    ("profligacy", "profligate"), ("proscribe", "proscription"), ("prudence", "prudent"),
    ("pugnacious", "pugnacity"), ("qualitative", "quality"), ("quiescence", "quiescent"),
    ("rapacious", "rapacity"), ("realism", "realistic"), ("recognition", "recognize", "recognizable"),
    ("regret", "regrettable"), ("reliance", "reliant"), ("remuneration", "remunerative"),
    ("repetitious", "repetitive"), ("reprehend", "reprehensible"), ("restrain", "restraint"),
    ("revelation", "revelatory"), ("revise", "revisionist"), ("revolution", "revolutionary"),
    ("sagacious", "sagacity"), ("salience", "salient"), ("scarce", "scarcity"),
    ("scrutinize", "scrutiny"), ("self-contradiction", "self-contradictory"),
    ("sensational", "sensationalism", "sensationalize"), ("serendipitous", "serendipity"),
    ("shortsighted", "shortsightedness"), ("shrink", "shrinkage"),
    ("significance", "signification", "signify"), ("snobbery", "snobbish"),
    ("solicitous", "solicitude"), ("solitary", "solitude"), ("stagnant", "stagnate"),
    ("stratification", "stratified"), ("stridency", "strident"), ("stringency", "stringent"),
    ("substance", "substantial"), ("subtle", "subtlety"), ("subversion", "subvert"),
    ("supplement", "supplementary"), ("suspicion", "suspicious"),
    ("sycophancy", "sycophant", "sycophantic"), ("symbiosis", "symbiotic"),
    ("symmetrical", "symmetry"), ("synopses", "synopsis"), ("synthesis", "synthesize", "synthetic"),
    ("tangent", "tangential"), ("tenacious", "tenacity"), ("timeliness", "timely"),
    ("traditionalism", "traditionalist"), ("transience", "transient"), ("trenchancy", "trenchant"),
    ("ubiquitous", "ubiquity"), ("undertaken", "undertaking", "undertook"),
    ("ungainliness", "ungainly"), ("intention", "unintended"), ("valediction", "valedictory"),
    ("variable", "variance", "variation"), ("vehemence", "vehement"), ("vexation", "vexatious"),
    ("vilification", "vilify"), ("virtuosic", "virtuoso"), ("whimsical", "whimsy"),
    ("zealot", "zealotry", "zealous"), ("inexact", "exactness"), ("incompetence", "competent"),
    ("deceitful", "deceptive"), ("candid", "candor"), ("deride", "derision"),
]

# Words that start like a negation but are not the opposite of a listed word.
NOT_NEGATED = {
    "illuminate", "illuminated", "illuminating", "immediate", "impassive", "impassivity",
    "infancy", "infinite", "infinitely", "infusion", "inhabit", "intimidated", "intimidating",
    "investigate", "investigation", "inverse", "inversely", "invocations", "informative",
    "unanimity", "nondescript", "unflagging", "intoxication", "interminable", "intrepid",
    "immaterial", "improvidence", "impersonal", "uninformed", "irresponsible", "inexact",
    "incompetence", "impassioned",
}
NEGATIONS = ("non", "un", "in", "im", "ir", "il")


def head(word):
    """The word a phrase entry is about: "conform to" -> "conform"."""
    parts = word.lower().split()
    return " ".join(parts[:-1]) if len(parts) > 1 and parts[-1] in PREPOSITIONS else word.lower()


def families(words):
    stemmer = SnowballStemmer("english")
    heads = {w: head(w) for w in words}
    by_head = {}
    for w in words:
        by_head.setdefault(heads[w], []).append(w)
    stem = {w: stemmer.stem(heads[w]) for w in words}
    split_key = {member: "split:" + min(group) for group in SPLIT for member in group}
    key = {w: split_key.get(heads[w], "stem:" + stem[w]) for w in words}

    parent = {w: w for w in words}
    negated = set()

    def find(w):
        while parent[w] != w:
            parent[w] = parent[parent[w]]
            w = parent[w]
        return w

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    first = {}
    for w in words:
        union(w, first.setdefault(key[w], w))
    for group in JOIN:
        present = [by_head[g][0] for g in group if g in by_head]
        missing = [g for g in group if g not in by_head]
        if missing:
            raise ValueError(f"JOIN names words that are not in the list: {missing}")
        for other in present[1:]:
            union(present[0], other)
    for w in words:
        h = heads[w]
        if h in NOT_NEGATED:
            continue
        for prefix in NEGATIONS:
            rest = h[len(prefix):]
            if not h.startswith(prefix) or len(rest) < 4:
                continue
            if rest in by_head:
                union(w, by_head[rest][0])
                negated.add(w)
                break
            target = stemmer.stem(rest)
            candidates = [x for x in words if stem[x] == target and x != w and not heads[x].startswith(prefix)]
            if candidates:
                best = max(candidates, key=lambda x: (len(commonprefix([heads[x], rest])), -len(x)))
                union(w, best)
                negated.add(w)
                break
    groups = {}
    for w in words:
        groups.setdefault(find(w), []).append(w)
    # Base forms first, shortest first; opposites ("inconsistent") follow.
    return [sorted(g, key=lambda w: (w in negated, len(heads[w]), w)) for g in groups.values()]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    with closing(sqlite3.connect(args.source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        words = list(dict.fromkeys(r[0] for r in db.execute("SELECT word FROM words ORDER BY group_number, position")))
    found = [f for f in families(words) if len(f) > 1]
    found.sort(key=lambda f: f[0].lower())
    args.target.write_text(
        "{\n" + '  "about": "Generated by scripts/build_word_families.py. Edit the lists in that script, not this file.",\n'
        '  "families": [\n' + ",\n".join("    " + json.dumps(f, ensure_ascii=False) for f in found) + "\n  ]\n}\n",
        encoding="utf-8",
    )
    print(f"{len(found)} families cover {sum(map(len, found))} of {len(words)} words")


if __name__ == "__main__":
    main()
