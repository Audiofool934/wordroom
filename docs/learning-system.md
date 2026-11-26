# Wordroom as a learning system

Status: design draft, 2026-11-26.
Prototype: `scripts/prototype_trail.py` (see the last section).

## Purpose

Wordroom helps a person learn words and learn about the world at the same time.
A word list is the entry point.
The words are met inside real ideas, in passages that are true and that cite their sources.
GRE is one possible word list, not the frame of the product.
Any list works: an exam list, the terms of a field, the words of a paper you are reading, or another language.

A language model does the work that used to need a teacher and a textbook author.
It picks what to study next, writes or finds the material, checks it, and answers questions about it.
Plain code does everything that does not need judgment: storage, scheduling, rendering and sync.

## Principles

1. **True before fluent.**
   A passage that teaches a fact must say only what its sources say.
   Every factual sentence links to the source passage that supports it.
   A second, independent check confirms each sentence against its source.
   A sentence that fails the check is not shown as a fact.
2. **Words in use, not in isolation.**
   A word is learned by meeting it several times, in different contexts that make its meaning clear.
3. **The learner's interests choose the ideas.**
   Material comes from topics the learner already cares about, so attention goes to the content, not to forcing interest.
4. **Agents prepare, the interface stays fast.**
   Content is generated ahead of time in jobs.
   Pages read finished content from storage, so they open and scroll without waiting for a model.
   Only the tutor answers live.
5. **Measure what is remembered.**
   Recall over time is the success signal.
   Time spent in the app is not.
6. **Bring your own data.**
   The repository ships no word lists, books or test questions.
   Lists are imported by the learner; definitions come from open sources or are generated.

## The learning loop

1. The **learner state** records what the learner knows: each word sense's memory strength, the ideas already read, interests and goals.
2. The **planner** chooses the next step: which words are due for review, which new words to add, and which idea can carry them.
3. **Content jobs** produce the material for that step: a trail of passages, sentences, structure notes and related words, each with sources.
4. The learner **reads, taps, recalls and asks**.
5. These **signals update the learner state**, and the loop starts again.

## Inputs

### Word lists

A list is a set of entries with at least a headword.
It may add a sense hint, a translation, a group or an order.
Import formats: CSV or TSV, plain text with one word on each line, an existing SQLite export, or the words pulled from a document the learner provides.

On import, Wordroom normalizes each entry:

- It finds the lemma and joins forms of the same root into a family (`scripts/build_word_families.py` does this today).
- It separates senses when one headword has several unrelated meanings.
- It takes a short definition from an open dictionary (Wiktionary or WordNet) or generates one, and records which.

A learner can keep several lists.
A word that appears in two lists is one word with one memory state.

### Sources

A source is a text the system may teach from: an encyclopedia article, a paper abstract, a book chapter the learner owns, or the learner's own notes.
Each source keeps its URL or file, the retrieval date, a revision or hash, and its license.
Sources are split into numbered paragraphs, so a claim can cite the exact paragraph that supports it.

## Data model

All tables live in the instance's SQLite database.
Generated content is stored, not regenerated on each view.

| Table | Holds |
| --- | --- |
| `lists`, `list_entries` | Imported lists and their entries, with order and group |
| `words`, `senses`, `families` | Normalized headwords, their senses with definitions, and root families |
| `sources`, `source_paragraphs` | Texts the system may teach from, split into citable paragraphs |
| `ideas` | Topics and claims, for example "FM synthesis made digital synthesizers affordable" |
| `passages`, `passage_sentences` | Generated text; each sentence lists the senses it uses and the paragraphs it cites |
| `checks` | The verifier's verdict for each sentence, with the model and time |
| `trails` | Ordered passages about one idea, with the target senses they introduce |
| `exposures` | Each time the learner meets a sense, in which sentence |
| `reviews` | Recall attempts and their results, as the score marks are today |
| `questions` | Questions the learner asks the tutor, with the context and the answer |

The current tables for score marks and the reading place stay and become part of `reviews` and the learner state.

## Agents and jobs

Each role is a job with a fixed input and a JSON output that code validates.
Jobs run in the background and save their results; a failed job leaves the old content in place.

| Role | Input | Output | When |
| --- | --- | --- | --- |
| Normalizer | A raw list | Lemmas, senses, families, definitions | On import |
| Planner | Learner state, interests, available sources | The next trail: an idea, target senses, sources to read | When the queue runs low |
| Retriever | An idea | Source paragraphs that cover it | Before writing |
| Writer | Idea, target senses, source paragraphs | Passages whose sentences cite paragraphs and mark target words | After retrieval |
| Verifier | One sentence and the paragraphs it cites | Supported, partly supported or unsupported, with a reason | After writing, by a separate call |
| Analyst | A sentence | Structure parts, skeleton and a note (as today) | After verification |
| Tutor | A question, the sentence and passage, the sources | An answer that cites sources or says it does not know | Live, when asked |

The writer and the verifier never share a call.
The verifier sees only the sentence and its cited paragraphs, not the writer's reasoning.
A sentence the verifier rejects is rewritten once from the same paragraphs, then dropped if it fails again.
Model checks still miss errors; the interface lets the learner flag a sentence, and a flagged sentence is not used again until reviewed.

## Review and the learner state

Memory is kept per sense, not per headword.
A review shows the sense in a new sentence from an idea the learner has already read, so recall also refreshes the idea.
Scheduling stays plain code: a simple interval rule now, and a fitted model such as FSRS when there is enough review data.
The planner reads due reviews first, then adds new senses up to a daily target.

## Interface

- **Word list:** the current scannable list, for any imported list, with the sentence view and structure breakdown.
- **Trails:** a reading page for one idea.
  Target words are marked; tapping one shows its sense and its other appearances.
  Each sentence has a small source marker; the sources and their licenses are listed at the end.
- **Word page:** every sentence and idea where a word has appeared, and its family.
- **Tutor:** ask about any sentence or word in place, with the answer citing sources.

## Architecture

The server stays Python standard library and SQLite, with no build step.
Today the word list is one page built at build time from one database.
The learning system moves to a content store with a small JSON API:

- Jobs write to the store.
- Pages are rendered from the store, still as static HTML plus a details file where that keeps phones fast.
- The learner state syncs through the API, as scores do now.

The provider layer stays in `providers.py` and supports any OpenAI-compatible endpoint.
Retrieval starts with an encyclopedia API and local files; other sources can be added behind the same interface.

## Measuring

- Recall rate per sense after 1, 7 and 30 days, compared between words met in trails and words met only in the list.
- Share of generated sentences the verifier rejects, and share the learner flags.
- Cost in tokens per learned sense.

## Phases

0. **Prototype trail** (this branch): one sourced, checked trail for one topic, rendered as a page.
   Question: is reading a trail clearly better than the list?
1. **Content store and list import:** the tables above, CSV and text import, open definitions, the word list built from the store.
2. **Planner and trails in the app:** trails generated from interests and due words, with exposures recorded.
3. **Tutor:** questions in context, answered with citations.
4. **More languages:** lists and sources in other languages, with the same loop.

## Open questions

- How strict should "true" be: is a supported verdict from a second model enough, or does the learner review each idea before it counts?
- Which sources are allowed by default, given their licenses?
- How should interests be captured: written once, inferred from reading, or both?
- Is the product for one learner on their own computer, or should several learners share an instance?

## Prototype notes

`scripts/prototype_trail.py` builds one trail end to end:

1. It reads a word list (any text list, or a SQLite export with `word` and `meaning_zh` columns).
2. It downloads the named encyclopedia articles and splits them into numbered paragraphs.
3. The planner call picks target words from the list that fit the topic.
4. The writer call writes passages whose sentences cite paragraph numbers and mark the target words.
5. A separate verifier call checks every sentence against the paragraphs it cites.
6. A sentence that fails is rewritten once and checked again, then dropped if it still fails.
7. It writes the trail as JSON and as a standalone HTML page.

The prototype runs outside the server and writes only to the output folder it is given.

## Prototype results (2026-11-26)

Trail: "How synthesizers learned to imitate any sound", from five Wikipedia articles, with a 4,703-word GRE list.
Model: DeepSeek Flash with thinking, about 56,000 prompt and 42,000 completion tokens for one trail.
Result: 5 passages, 38 sentences, 24 of 25 target words used; every sentence cites its paragraphs and passed the separate check.

What the first run taught:

- A rewrite told to keep a target word kept it inside a disclaimer ("though the paragraph does not describe it as sophisticated").
  The support check passed it, because the sentence was true.
  The prototype now rejects any sentence that talks about its sources, and a rewrite may drop a word instead.
- Asking for "inferable" meanings produced inline definitions ("intricate, that is, complex").
  The writer now uses words as an author would and never glosses them.
- The planner chose words of praise the sources never use (sophisticated, influential).
  It now picks only words the source facts can carry.

What is still open:

- Some sentences stay very close to the source wording; a style pass or a paraphrase check may be needed.
- Some target words are easy (generate, modify); the planner needs the learner's known words to aim higher.
- A word used in another form ("reproducing" for "reproduce") is not marked; matching should use the lemma.
- The checker judges support, not quality; repetition and flow need their own check.
