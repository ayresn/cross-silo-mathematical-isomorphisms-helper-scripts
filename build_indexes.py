#!/usr/bin/env python3
"""
build_indexes.py — generate directory views for the Cross-Silo Mathematical
Isomorphisms repository from entry YAML front matter.

Front matter is the single source of truth. Every index is a derived artifact
and should never be hand-edited; edit the entry, then re-run this.

Outputs (into --out, default ./indexes):
    index-by-domain.md    every entry cross-listed under both of its domains
    index-by-status.md    grouped by lifecycle state
    index-by-model.md     grouped by generating model
    index-by-family.md    grouped by structural_family
    stage-3-queue.md      cleared + flagged, ascending reject-vote share
    entries.json          machine-readable, for a sortable web view

Usage:
    python3 tools/build_indexes.py                 # generate everything
    python3 tools/build_indexes.py --check         # lint only, no writes
    python3 tools/build_indexes.py --root . --out indexes

Requires: PyYAML  (pip install pyyaml)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required:  pip install pyyaml")


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------

# Slugs are lowercase-hyphenated; naive .title() mangles acronyms and
# possessives. Anything in this map is substituted after title-casing.
ACRONYMS = {
    "Mhd": "MHD", "Sph": "SPH", "Gp": "GP", "Llm": "LLM", "Nlp": "NLP",
    "Sgd": "SGD", "Admm": "ADMM", "Sis": "SIS", "Sir": "SIR", "Sirs": "SIRS",
    "Ode": "ODE", "Pde": "PDE", "Ai": "AI", "Ml": "ML", "Rl": "RL",
    "Hjb": "HJB", "Kdv": "KdV", "Lwr": "LWR", "Nlse": "NLSE", "Eps": "EPS",
    "Dna": "DNA", "Rna": "RNA", "Ct": "CT", "Mri": "MRI", "Les": "LES",
    "Cfd": "CFD", "Fem": "FEM", "Amm": "AMM", "Defi": "DeFi", "Hft": "HFT",
    "Jmak": "JMAK", "Gtn": "GTN", "Dglap": "DGLAP", "Qed": "QED",
}

STATUS_ORDER = [
    "validated-candidate",
    "adversarial-cleared",
    "adversarial-flagged",
    "candidate",
    "held",
    "failed-validation",
    "adversarial-rejected",
]

STATUS_LABEL = {
    "validated-candidate": ("Stage 3 / validated", "Passed human bibliometric validation."),
    "adversarial-cleared": ("Stage 2 / cleared", "No reviewer voted to reject. Highest priority for Stage 3."),
    "adversarial-flagged": ("Stage 2 / flagged", "Advanced to Stage 3 with reviewer watch items."),
    "candidate": ("Stage 1 / pending", "Generated, not yet adversarially reviewed."),
    "held": ("Stage 1 / held", "Failed automated pre-screen; not submitted to a panel."),
    "failed-validation": ("Stage 3 / rejected", "Failed human bibliometric validation."),
    "adversarial-rejected": ("Stage 2 / rejected", "Majority of reviewers voted to reject. Retained for false-positive-rate tracking; not a research lead."),
}

GENERATED_BANNER = (
    "<!-- GENERATED FILE — DO NOT EDIT BY HAND.\n"
    "     Produced by tools/build_indexes.py from entry YAML front matter.\n"
    "     Edit the entry file and re-run the script. -->\n"
)


def prettify(slug: str) -> str:
    """'power-system-voltage-stability' -> 'Power System Voltage Stability'."""
    if not slug:
        return ""
    words = str(slug).replace("_", "-").split("-")
    out = [ACRONYMS.get(w.capitalize(), w.capitalize()) for w in words if w]
    return " ".join(out)


def pct(n: int, d: int) -> str:
    return "—" if not d else f"{round(100 * n / d)}%"


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Review:
    reviewer: str
    verdict: str
    timestamp: str = ""
    rationale: str = ""


@dataclass
class Entry:
    entry_id: str                 # "SID-040"
    number: int                   # 40
    path: str                     # "mappings-anthropic-claude/claude-sonnet-5_entry-040.md"
    maturity_stage: str
    company: str
    model_family: str
    model_version: str
    generated: str
    domain_a: str
    domain_b: str
    structural_family: str
    vectors: list[str] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    synthesis: str = ""           # display title, from README or YAML
    isomorphism: str = ""         # display "X mapped to Y"
    problems: list[str] = field(default_factory=list)

    # -- derived -----------------------------------------------------------
    @property
    def n_reviews(self) -> int:
        return len(self.reviews)

    @property
    def n_reject(self) -> int:
        return sum(1 for r in self.reviews if r.verdict.upper() == "REJECT")

    @property
    def reject_share(self) -> float | None:
        return None if not self.reviews else self.n_reject / self.n_reviews

    @property
    def reject_pct(self) -> str:
        return pct(self.n_reject, self.n_reviews)

    @property
    def model_label(self) -> str:
        return f"{self.company} {self.model_family} {self.model_version}".strip()

    @property
    def domains(self) -> list[str]:
        return [d for d in (self.domain_a, self.domain_b) if d]

    @property
    def title(self) -> str:
        return self.synthesis or f"{prettify(self.domain_a)} & {prettify(self.domain_b)}"

    def expected_stage(self) -> str | None:
        """Lifecycle state implied by the panel vote, per the aggregation rules."""
        if not self.reviews:
            return None
        if self.n_reject == 0:
            return "adversarial-cleared"
        if self.n_reject >= 4:
            return "adversarial-rejected"
        return "adversarial-flagged"

    def link(self, from_dir: bool = True) -> str:
        """Markdown link. Indexes live one level down, so prefix with '../'."""
        prefix = "../" if from_dir else ""
        return f"[{self.entry_id}]({prefix}{self.path})"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)

README_ENTRY_RE = re.compile(
    r"^\*\s+\*\*\[(?P<label>[^\]]+)\]\((?P<path>[^)]+)\)\*\*"
    r"[^\n]*?`Stage\s*(?P<stage>[0-9])\s*/\s*(?P<state>\w+)`"
    r"(?:[^\n]*?\((?P<pct>\d+)%[^\n]*?\))?[^\n]*\n"
    r"(?:\s+\*\s+\*System Synthesis:\*\s*(?P<synth>[^\n]+)\n)?"
    r"(?:\s+\*\s+\*Domains:\*\s*(?P<domains>[^\n]+)\n)?"
    r"(?:\s+\*\s+\*Isomorphism:\*\s*(?P<iso>[^\n]+)\n)?",
    re.M,
)


def read_front_matter(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = FM_RE.match(text)
    if not m:
        return None
    try:
        return yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"unparseable YAML front matter: {exc}") from exc


def parse_readme(readme: Path) -> dict[int, dict]:
    """Pull display strings out of the legacy README directory, keyed by number.

    System Synthesis and Isomorphism currently live only in the README (the
    extraction protocol emits them as a separate snippet). Until they move
    into the entries' front matter, this is where they come from.
    """
    if not readme.exists():
        return {}
    text = readme.read_text(encoding="utf-8").replace("\r\n", "\n")
    found: dict[int, dict] = {}
    for m in README_ENTRY_RE.finditer(text):
        num = re.search(r"entry-(\d+)", m.group("path") or "")
        if not num:
            continue
        found[int(num.group(1))] = {
            "synthesis": (m.group("synth") or "").strip(),
            "isomorphism": (m.group("iso") or "").strip(),
            "state": m.group("state"),
            "pct": int(m.group("pct")) if m.group("pct") else None,
        }
    return found


def load_entries(root: Path) -> list[Entry]:
    readme_display = parse_readme(root / "README.md")
    entries: list[Entry] = []

    for md in sorted(root.glob("mappings-*/*.md")):
        rel = md.relative_to(root).as_posix()
        try:
            fm = read_front_matter(md)
        except ValueError as exc:
            entries.append(_broken(rel, str(exc)))
            continue
        if not fm:
            entries.append(_broken(rel, "no YAML front matter found"))
            continue

        sid = fm.get("sid_metadata") or {}
        prov = fm.get("provenance") or {}
        iso = fm.get("isomorphism_metadata") or {}
        vstat = fm.get("validation_status") or {}

        entry_id = str(sid.get("entry_id", "")).strip()
        num_m = re.search(r"(\d+)", entry_id) or re.search(r"entry-(\d+)", rel)
        number = int(num_m.group(1)) if num_m else -1

        reviews = [
            Review(
                reviewer=str(v.get("reviewer_model", "unknown")),
                verdict=str(v.get("verdict", "")).upper(),
                timestamp=str(v.get("review_timestamp", "")),
                rationale=str(v.get("verdict_rationale", "")),
            )
            for k, v in vstat.items()
            if k.endswith("_adversarial_review") and isinstance(v, dict)
        ]

        e = Entry(
            entry_id=entry_id or f"(unknown:{rel})",
            number=number,
            path=rel,
            maturity_stage=str(sid.get("maturity_stage", "")).strip(),
            company=str(prov.get("company", "")).strip(),
            model_family=str(prov.get("model_family", "")).strip(),
            model_version=str(prov.get("model_version", "")).strip(),
            generated=str(prov.get("generation_timestamp", "")).strip(),
            domain_a=str(iso.get("domain_a", "")).strip(),
            domain_b=str(iso.get("domain_b", "")).strip(),
            structural_family=str(iso.get("structural_family", "")).strip(),
            vectors=list(iso.get("triple_correspondence_vectors") or []),
            reviews=reviews,
        )

        disp = fm.get("display") or {}
        rd = readme_display.get(number, {})
        e.synthesis = str(disp.get("system_synthesis") or rd.get("synthesis") or "").strip()
        e.isomorphism = str(disp.get("isomorphism") or rd.get("isomorphism") or "").strip()

        _validate(e, rd)
        entries.append(e)

    return sorted(entries, key=lambda x: x.number)


def _broken(rel: str, msg: str) -> Entry:
    e = Entry(entry_id=f"(unreadable:{rel})", number=-1, path=rel, maturity_stage="",
              company="", model_family="", model_version="", generated="",
              domain_a="", domain_b="", structural_family="")
    e.problems.append(msg)
    return e


def _validate(e: Entry, readme_row: dict) -> None:
    """Consistency checks. These are the point of running with --check."""
    if not e.maturity_stage:
        e.problems.append("missing sid_metadata.maturity_stage")
    elif e.maturity_stage not in STATUS_LABEL:
        e.problems.append(f"unknown maturity_stage '{e.maturity_stage}'")

    if not e.domain_a or not e.domain_b:
        e.problems.append("missing domain_a and/or domain_b")
    if not e.structural_family:
        e.problems.append("missing structural_family")

    if re.search(r"entry-0*(\d+)", e.path):
        file_num = int(re.search(r"entry-0*(\d+)", e.path).group(1))
        if file_num != e.number:
            e.problems.append(f"entry_id {e.entry_id} does not match filename number {file_num}")

    if e.generated:
        try:
            g = date.fromisoformat(e.generated)
            if g > date.today():
                e.problems.append(f"generation_timestamp {e.generated} is in the future")
        except ValueError:
            e.problems.append(f"generation_timestamp '{e.generated}' is not an ISO date")

    exp = e.expected_stage()
    if exp and e.maturity_stage and exp != e.maturity_stage:
        e.problems.append(
            f"maturity_stage is '{e.maturity_stage}' but {e.n_reject}/{e.n_reviews} "
            f"reject votes imply '{exp}'"
        )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def bullet(e: Entry, *, show_status: bool = True, show_model: bool = False,
           show_domains: bool = True) -> str:
    tag = STATUS_LABEL.get(e.maturity_stage, (e.maturity_stage or "unknown", ""))[0]
    head = f"* **{e.link()}** — {e.title}"
    bits = []
    if show_status:
        s = f"`{tag}`"
        if e.reviews:
            s += f" ({e.reject_pct} voted to reject)"
        bits.append(s)
    if show_domains:
        bits.append(f"*Domains:* {prettify(e.domain_a)} & {prettify(e.domain_b)}")
    if show_model:
        bits.append(f"*Model:* {e.model_label}")
    if e.isomorphism:
        bits.append(f"*Isomorphism:* {e.isomorphism}")
    return head + "\n" + "".join(f"  * {b}\n" for b in bits)


def header(title: str, blurb: str) -> str:
    return f"{GENERATED_BANNER}\n# {title}\n\n{blurb}\n\n[← Back to README](../README.md)\n\n---\n\n"


def render_by_domain(entries: list[Entry]) -> str:
    buckets: dict[str, list[Entry]] = {}
    for e in entries:
        for d in e.domains:
            buckets.setdefault(d, []).append(e)

    out = header(
        "Index by Domain",
        "Every entry appears under **both** of its domains. If your field is listed, "
        "the entries beneath it claim a structural correspondence between your field "
        "and another one — and need someone who knows your field to say whether the "
        "correspondence holds.\n\n"
        "No entry here is verified. See the README for what the status tags mean.",
    )
    out += f"{len(buckets)} domains across {len(entries)} entries.\n\n"
    for d in sorted(buckets, key=lambda x: prettify(x).lower()):
        rows = sorted(buckets[d], key=lambda e: (STATUS_ORDER.index(e.maturity_stage)
                                                 if e.maturity_stage in STATUS_ORDER else 99,
                                                 e.number))
        out += f"## {prettify(d)}\n\n"
        for e in rows:
            other = e.domain_b if e.domain_a == d else e.domain_a
            tag = STATUS_LABEL.get(e.maturity_stage, (e.maturity_stage, ""))[0]
            share = f" ({e.reject_pct} voted to reject)" if e.reviews else ""
            out += (f"* **{e.link()}** — {e.title}\n"
                    f"  * *Paired with:* {prettify(other)}\n"
                    f"  * `{tag}`{share}\n")
        out += "\n"
    return out


def render_by_status(entries: list[Entry]) -> str:
    out = header(
        "Index by Lifecycle State",
        "Entries grouped by where they sit in the three-stage pipeline. "
        "Rejected entries are retained deliberately: they are what makes the "
        "pipeline's false-positive rate computable.",
    )
    for st in STATUS_ORDER:
        rows = [e for e in entries if e.maturity_stage == st]
        if not rows:
            continue
        label, blurb = STATUS_LABEL[st]
        rows.sort(key=lambda e: (e.reject_share if e.reject_share is not None else 1.0, e.number))
        out += f"## `{label}` — {len(rows)} entries\n\n{blurb}\n\n"
        for e in rows:
            out += bullet(e, show_status=False, show_model=True)
        out += "\n"
    return out


def render_by_model(entries: list[Entry]) -> str:
    buckets: dict[str, list[Entry]] = {}
    for e in entries:
        buckets.setdefault(e.model_label or "(unknown)", []).append(e)

    out = header(
        "Index by Generating Model",
        "Which model produced each candidate. Useful for comparing generator "
        "quality; not useful for finding research leads — try the domain index for that.",
    )
    out += "| Model | Entries | Reviewed | Rejected | Survived | Mean reject-vote |\n"
    out += "| --- | ---: | ---: | ---: | ---: | ---: |\n"
    for m in sorted(buckets):
        rows = buckets[m]
        rev = [e for e in rows if e.reviews]
        rej = [e for e in rev if e.maturity_stage == "adversarial-rejected"]
        mean = (sum(e.reject_share for e in rev) / len(rev)) if rev else None
        out += (f"| {m} | {len(rows)} | {len(rev)} | {len(rej)} | "
                f"{pct(len(rev) - len(rej), len(rev))} | "
                f"{f'{100 * mean:.1f}%' if mean is not None else '—'} |\n")
    out += "\n---\n\n"
    for m in sorted(buckets):
        out += f"## {m}\n\n"
        for e in sorted(buckets[m], key=lambda e: e.number):
            out += bullet(e)
        out += "\n"
    return out


FAMILY_STOPWORDS = {"and", "of", "with", "the", "for", "in", "on", "a"}


def family_tokens(slug: str) -> set[str]:
    return {t for t in str(slug).replace("_", "-").split("-")
            if t and t not in FAMILY_STOPWORDS}


def cluster_families(families: list[str], threshold: float = 0.34) -> list[list[str]]:
    """Group family labels whose token sets overlap.

    structural_family is free text, so two generators describing the same
    structure rarely produce the same string ('nonlinear-wave-instabilities'
    vs 'nonlinear-wave-propagation'). Exact-match grouping therefore reports
    no concentration even when concentration exists. This finds the overlap
    by Jaccard similarity on tokens, then takes connected components.
    """
    toks = {f: family_tokens(f) for f in families}
    parent = {f: f for f in families}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(families):
        for b in families[i + 1:]:
            ta, tb = toks[a], toks[b]
            if not ta or not tb:
                continue
            jac = len(ta & tb) / len(ta | tb)
            if jac >= threshold or len(ta & tb) >= 2:
                parent[find(a)] = find(b)

    groups: dict[str, list[str]] = {}
    for f in families:
        groups.setdefault(find(f), []).append(f)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: -len(g))


def render_by_family(entries: list[Entry]) -> str:
    buckets: dict[str, list[Entry]] = {}
    for e in entries:
        buckets.setdefault(e.structural_family or "(unspecified)", []).append(e)

    ranked = sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    total = len(entries)
    top = sum(len(v) for _, v in ranked[:5])

    out = header(
        "Index by Structural Family",
        "The shared mathematical structure each entry claims, independent of subject matter. "
        "This is the closest thing the dataset has to an atlas view.\n\n"
        "**It is also a diagnostic.** If a small number of families account for most entries, "
        "the generator is drawing from a narrow structural vocabulary and dressing it in "
        "different domain clothing. Watch the concentration figure below.",
    )
    out += (f"**{len(buckets)} families across {total} entries.** "
            f"The five largest account for {top}/{total} ({pct(top, total)}).\n\n")
    out += "| Structural family | Entries |\n| --- | ---: |\n"
    for fam, rows in ranked:
        out += f"| {prettify(fam)} | {len(rows)} |\n"

    # -- Free-text variation makes exact-match grouping useless as a --------
    # -- concentration measure. These two views correct for that.  ---------
    counts: dict[str, int] = {}
    for e in entries:
        for t in family_tokens(e.structural_family):
            counts[t] = counts.get(t, 0) + 1
    recurring = sorted(((t, c) for t, c in counts.items() if c > 1),
                       key=lambda kv: (-kv[1], kv[0]))

    out += "\n## Structural vocabulary\n\n"
    out += ("`structural_family` is free text, so two entries describing the same "
            "structure often carry different labels and the table above understates "
            "concentration. Token frequency sees through that.\n\n")
    if recurring:
        out += "| Token | Entries |\n| --- | ---: |\n"
        for t, c in recurring:
            out += f"| `{t}` | {c} |\n"
    else:
        out += "_No token recurs across entries._\n"

    clusters = [g for g in cluster_families(sorted(buckets)) if len(g) > 1]
    out += "\n## Near-duplicate families\n\n"
    if clusters:
        clustered = sum(len(buckets[f]) for g in clusters for f in g)
        out += (f"{len(clusters)} clusters of labels that overlap enough to plausibly "
                f"describe the same structure, covering {clustered}/{total} entries "
                f"({pct(clustered, total)}). **Treat this, not the table above, as the "
                f"concentration estimate.**\n\n")
        for g in clusters:
            n = sum(len(buckets[f]) for f in g)
            out += f"* **{n} entries** — " + "; ".join(prettify(f) for f in g) + "\n"
    else:
        out += "_No overlapping family labels detected._\n"

    out += "\n---\n\n"
    for fam, rows in ranked:
        out += f"## {prettify(fam)} ({len(rows)})\n\n"
        for e in sorted(rows, key=lambda e: e.number):
            out += bullet(e, show_model=True)
        out += "\n"
    return out


def render_stage3_queue(entries: list[Entry]) -> str:
    queue = [e for e in entries
             if e.maturity_stage in ("adversarial-cleared", "adversarial-flagged")]
    queue.sort(key=lambda e: (e.reject_share if e.reject_share is not None else 1.0, e.number))

    out = header(
        "Stage 3 Queue",
        "Entries that survived adversarial review and are waiting on human bibliometric "
        "validation. Ordered by ascending reject-vote share, which is the best available "
        "proxy for where expert time is worth spending.\n\n"
        "**If one of these is in your field, the ask is small:** does the correspondence "
        "hold, and does it already exist in your literature? A comment saying \"no, this "
        "fails because X\" is as useful as a confirmation.",
    )
    if not queue:
        return out + "_Queue is empty._\n"
    out += f"{len(queue)} entries awaiting Stage 3.\n\n"
    for i, e in enumerate(queue, 1):
        out += (f"1. **{e.link()}** — {e.title}\n"
                f"   * *Domains:* {prettify(e.domain_a)} & {prettify(e.domain_b)}\n"
                f"   * *Reject-vote share:* {e.reject_pct} "
                f"({e.n_reject}/{e.n_reviews} reviewers)\n")
        if e.isomorphism:
            out += f"   * *Isomorphism:* {e.isomorphism}\n"
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

VIEWS = {
    "index-by-domain.md": render_by_domain,
    "index-by-status.md": render_by_status,
    "index-by-model.md": render_by_model,
    "index-by-family.md": render_by_family,
    "stage-3-queue.md": render_stage3_queue,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("."), help="repository root")
    ap.add_argument("--out", type=Path, default=None, help="output dir (default <root>/indexes)")
    ap.add_argument("--check", action="store_true",
                    help="report problems and exit nonzero; write nothing")
    args = ap.parse_args()

    root = args.root.resolve()
    out = (args.out or root / "indexes").resolve()

    entries = load_entries(root)
    if not entries:
        print(f"No entries found under {root}/mappings-*/", file=sys.stderr)
        return 1

    flagged = [e for e in entries if e.problems]
    for e in flagged:
        print(f"{e.path}")
        for p in e.problems:
            print(f"    ! {p}")
    n_problems = sum(len(e.problems) for e in flagged)

    print(f"\n{len(entries)} entries — {n_problems} problems in {len(flagged)} files")
    reviewed = [e for e in entries if e.reviews]
    if reviewed:
        rej = sum(1 for e in reviewed if e.maturity_stage == "adversarial-rejected")
        print(f"{len(reviewed)} reviewed | {rej} rejected ({pct(rej, len(reviewed))}) "
              f"| {len(reviewed) - rej} advanced")
    fams = {e.structural_family for e in entries if e.structural_family}
    print(f"{len(fams)} structural families across {len(entries)} entries")

    if args.check:
        return 1 if n_problems else 0

    out.mkdir(parents=True, exist_ok=True)
    for name, fn in VIEWS.items():
        (out / name).write_text(fn(entries), encoding="utf-8")
        print(f"wrote {out.relative_to(root) / name}")

    payload = []
    for e in entries:
        d = asdict(e)
        d.pop("problems", None)
        d.update(reject_share=e.reject_share, n_reject=e.n_reject,
                 n_reviews=e.n_reviews, title=e.title, model_label=e.model_label)
        payload.append(d)
    (out / "entries.json").write_text(
        json.dumps({"generated": date.today().isoformat(), "entries": payload}, indent=2),
        encoding="utf-8")
    print(f"wrote {out.relative_to(root) / 'entries.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
