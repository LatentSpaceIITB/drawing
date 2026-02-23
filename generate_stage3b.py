"""
Stage 3B — Linguistic Variants (LLM-assisted)
Reads canonical_descriptions.json, writes text_variants.json.
Requires: anthropic SDK, ANTHROPIC_API_KEY env var.
"""

import json
import os
import sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("ERROR: 'anthropic' package not installed. Run: pip install anthropic")
    sys.exit(1)

MODEL = "claude-haiku-4-5-20251001"
VARIANTS_PER_FRAGMENT = 5
BATCH_SIZE = 10

SYSTEM_PROMPT = (
    "You are a technical writer specializing in process engineering and P&ID documentation. "
    "Given fragment descriptions, produce exactly 5 paraphrases for each. "
    "Rules:\n"
    "- Preserve all control roles (valve, instrumentation), numeric counts, and directionality\n"
    "- Do not introduce new entities or equipment not in the original\n"
    "- Use varied vocabulary from piping, instrumentation, and process engineering domains\n"
    "Return a JSON object mapping fragment_id to a list of 5 variant strings:\n"
    '{ "fragment_id_here": ["variant1", ..., "variant5"], ... }'
)


def chunk(lst: list, n: int) -> list[list]:
    return [lst[i : i + n] for i in range(0, len(lst), n)]


def call_llm_batch(batch: list[dict], client: anthropic.Anthropic) -> dict[str, list[str]]:
    """Call the LLM with a batch, return {fragment_id: [variants]}."""
    user_msg = json.dumps(
        [
            {
                "fragment_id": item["fragment_id"],
                "subtype": item["fragment_subtype"],
                "canonical_text": item["canonical_text"],
            }
            for item in batch
        ],
        indent=2,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return json.loads(raw)


def call_llm_single(item: dict, client: anthropic.Anthropic) -> dict[str, list[str]]:
    """Fallback: call LLM for a single fragment."""
    return call_llm_batch([item], client)


def with_fallback(
    batch: list[dict], client: anthropic.Anthropic
) -> dict[str, list[str]]:
    """Try batch call; fall back to individual calls on parse failure."""
    try:
        return call_llm_batch(batch, client)
    except (json.JSONDecodeError, KeyError, Exception) as e:
        print(f"    Batch parse failed ({e}), retrying individually...")
        result = {}
        for item in batch:
            try:
                single = call_llm_single(item, client)
                result.update(single)
            except Exception as e2:
                print(f"    Single call failed for {item['fragment_id']}: {e2}")
                # Provide empty placeholders so we don't lose track
                result[item["fragment_id"]] = []
        return result


def main():
    src = Path("canonical_descriptions.json")
    dst = Path("text_variants.json")

    if not src.exists():
        print(f"ERROR: {src} not found. Run generate_stage3a.py first.")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    with open(src) as f:
        data = json.load(f)

    descriptions = data["descriptions"]
    total = len(descriptions)
    client = anthropic.Anthropic(api_key=api_key)

    batches = chunk(descriptions, BATCH_SIZE)
    print(f"[3B] Processing {total} fragments in {len(batches)} batches of {BATCH_SIZE}...")

    all_variants: dict[str, list[str]] = {}

    for i, batch in enumerate(batches):
        ids = [item["fragment_id"] for item in batch]
        print(f"  Batch {i+1}/{len(batches)} ({ids[0]} … {ids[-1]})...", end=" ", flush=True)
        result = with_fallback(batch, client)
        all_variants.update(result)
        # Count how many we got
        got = sum(1 for fid in ids if fid in all_variants and all_variants[fid])
        print(f"ok ({got}/{len(batch)})")

    # Build output list preserving original order
    variants_list = []
    for desc in descriptions:
        fid = desc["fragment_id"]
        variants = all_variants.get(fid, [])
        variants_list.append({
            "fragment_id": fid,
            "canonical_text": desc["canonical_text"],
            "variants": variants,
        })

    output = {
        "metadata": {
            "source_file": "canonical_descriptions.json",
            "model": MODEL,
            "variants_per_fragment": VARIANTS_PER_FRAGMENT,
            "total_fragments": total,
        },
        "variants": variants_list,
    }

    with open(dst, "w") as f:
        json.dump(output, f, indent=2)

    # Verification
    assert len(variants_list) == total, f"Expected {total} entries, got {len(variants_list)}"
    wrong_count = [
        v["fragment_id"]
        for v in variants_list
        if len(v["variants"]) != VARIANTS_PER_FRAGMENT
    ]
    if wrong_count:
        print(f"  WARNING: {len(wrong_count)} fragments without exactly {VARIANTS_PER_FRAGMENT} variants: {wrong_count[:5]}...")

    identical = [
        v["fragment_id"]
        for v in variants_list
        if v["canonical_text"] in v["variants"]
    ]
    if identical:
        print(f"  WARNING: {len(identical)} fragments have a variant identical to canonical text.")

    success = total - len(wrong_count)
    print(f"[3B] Wrote {dst} — {total} entries ({success} with full {VARIANTS_PER_FRAGMENT} variants).")


if __name__ == "__main__":
    main()
