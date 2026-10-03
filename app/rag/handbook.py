import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import json5

STOP_WORDS = set("a an and are as at be by can do does for from how i in is it me of on or please the this to was what when where which who why with you your tell about".split())


def tokenize(text: str) -> list[str]:
    return [term for term in re.findall(r"\w+", text.casefold()) if term not in STOP_WORDS]


def published_text(record: dict) -> list[str]:
    parts = []
    for key in ("kicker", "title", "summary", "body", "bullets", "callout", "searchTerms"):
        value = record.get(key, [])
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(item for item in value if isinstance(item, str))
    for sample in record.get("emailSamples", []):
        parts.extend(sample[key] for key in ("title", "subject") if isinstance(sample.get(key), str))
        for block in sample.get("blocks", []):
            for key in ("text", "label", "before", "after"):
                if isinstance(block.get(key), str):
                    parts.append(block[key])
            if block.get("type") == "details":
                for item in block.get("items", []):
                    parts.extend(item[key] for key in ("label", "value") if isinstance(item.get(key), str))
            elif block.get("type") in ("list", "signature"):
                parts.extend(item for item in block.get("items", block.get("lines", [])) if isinstance(item, str))
    return parts


@dataclass(frozen=True)
class Chunk:
    source_id: str
    file: str
    chapter_id: str
    section_id: str
    title: str
    text: str

    def source(self) -> dict:
        return {key: getattr(self, key) for key in ("source_id", "file", "chapter_id", "section_id", "title")}


class HandbookIndex:
    def __init__(self, path: Path, chunk_chars: int = 2400):
        if path.suffix != ".ts":
            raise ValueError("HANDBOOK_PATH must point to a .ts file")
        source = path.read_text(encoding="utf-8")
        self._build(source, path.name, chunk_chars)

    @classmethod
    def from_source(cls, source: str, filename: str, chunk_chars: int = 2400):
        index = cls.__new__(cls)
        try:
            index._build(source, filename, chunk_chars)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            raise ValueError("Invalid handbook export or chapter/section schema") from exc
        return index

    def _build(self, source: str, filename: str, chunk_chars: int):
        match = re.search(r"export\s+const\s+howIvyWorksHandbook\s*(?::[^=]+)?=\s*", source)
        if not match:
            raise ValueError("Missing howIvyWorksHandbook export")
        literal = source[match.end():].strip().removesuffix(";").strip()
        chapters = json5.loads(literal)
        if not isinstance(chapters, list) or not chapters:
            raise ValueError("The handbook must contain a nonempty chapter array")
        self.chunks: list[Chunk] = []
        self.section_count = 0
        for chapter in chapters:
            self._add_record(filename, chapter, chapter, chunk_chars)
            for section in chapter.get("sections", []):
                self.section_count += 1
                self._add_record(filename, chapter, section, chunk_chars)
        if not self.chunks:
            raise ValueError("The handbook has no published content")
        self.terms = [Counter(tokenize(chunk.text)) for chunk in self.chunks]
        self.lengths = [sum(terms.values()) for terms in self.terms]
        self.average_length = sum(self.lengths) / len(self.lengths) or 1
        self.frequencies = Counter(term for terms in self.terms for term in terms)
        self.file = filename
        self.chapter_count = len(chapters)

    def _add_record(self, file: str, chapter: dict, record: dict, limit: int):
        if not isinstance(record, dict) or any(
            not isinstance(record.get(key), str) or not record[key].strip()
            for key in ("id", "title")
        ):
            raise ValueError("Chapters and sections require nonempty string ids and titles")
        text = "\n".join(published_text(record)).strip()
        title = record["title"]
        heading = f"{chapter['title']} / {title}\n"
        start = 0
        part = 1
        while start < len(text):
            end = min(start + limit, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start + limit // 2, end)
                if boundary > start:
                    end = boundary
            self.chunks.append(Chunk(
                source_id=f"{chapter['id']}/{record['id']}:{part}",
                file=file, chapter_id=chapter["id"], section_id=record["id"],
                title=title, text=heading + text[start:end],
            ))
            if end == len(text):
                break
            start = max(start + 1, end - 200)
            part += 1

    def search(self, query: str, top_k: int = 6) -> list[Chunk]:
        query_terms = set(tokenize(query))
        ranked = []
        count = len(self.chunks)
        for index, terms in enumerate(self.terms):
            score = 0.0
            for term in query_terms:
                frequency = terms.get(term, 0)
                if not frequency:
                    continue
                idf = math.log(1 + (count - self.frequencies[term] + 0.5) / (self.frequencies[term] + 0.5))
                denominator = frequency + 1.5 * (0.25 + 0.75 * self.lengths[index] / self.average_length)
                score += idf * frequency * 2.5 / denominator
            if score > 0:
                ranked.append((score, index))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [self.chunks[index] for _, index in ranked[:top_k]]

    def retrieve(self, question: str, history: list[dict], top_k: int) -> list[Chunk]:
        terms = tokenize(question)
        followup = len(terms) <= 4 and re.search(r"\b(it|that|those|they|them|this|also|more)\b", question, re.I)
        if followup:
            previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
            question = f"{question} {previous}"
        return self.search(question, top_k)
