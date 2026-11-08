"""Ask questions from the command line (manual testing).

    python -m app.ask "What is the exit load of HDFC Small Cap Fund?"
    python -m app.ask "Who manages it?" --scheme hdfc-large-cap --debug
    python -m app.ask --chat              # conversation with follow-ups ("and its NAV?")

The router works out the scheme from the question (or the conversation); --scheme forces
one.
"""

import argparse
import json
import logging
import os
import sys


def _setup_logging(debug: bool) -> None:
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.basicConfig(level=logging.INFO if debug else logging.WARNING,
                        format="%(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "sentence_transformers", "huggingface_hub", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print(response, debug: bool) -> None:
    print(response.render())
    if debug:
        print("\n" + json.dumps({"kind": response.kind, "cited": response.source_chunk_id,
                                 "thread_scheme": response.scheme, **response.debug},
                                indent=2, ensure_ascii=False))


def chat(pipeline, debug: bool) -> int:
    from app.router import ConversationContext

    context = ConversationContext()
    print("Facts-only. No investment advice.  (empty line or Ctrl+C to quit)\n")
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            return 0
        response = pipeline.answer(question, context=context)
        print()
        _print(response, debug)
        print()
        # Thread state, as Phase 5 will store it: PII messages are never kept.
        if response.kind != "pii_blocked":
            context.history += [("user", question), ("assistant", response.text)]
            context.history = context.history[-6:]
        context.last_scheme = response.scheme


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ask")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--scheme", default=None,
                        help="force a scheme id, e.g. hdfc-mid-cap (see data/schemes.yaml)")
    parser.add_argument("--chat", action="store_true", help="interactive conversation")
    parser.add_argument("--debug", action="store_true", help="print routing/retrieval details")
    args = parser.parse_args(argv)
    if not args.chat and not args.question:
        parser.error("give a question, or use --chat")
    _setup_logging(args.debug)

    from app.pipeline import build_pipeline
    from app.registry import load_schemes

    if args.scheme and args.scheme not in load_schemes():
        parser.error(f"unknown scheme {args.scheme!r}; choose from: "
                     f"{', '.join(load_schemes())}")
    pipeline = build_pipeline()
    if args.chat:
        return chat(pipeline, args.debug)
    _print(pipeline.answer(args.question, scheme=args.scheme), args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
