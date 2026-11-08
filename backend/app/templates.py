"""Fixed response texts. Each is at most 3 sentences; the composer adds the single link
and the footer (except for the PII warning, which has neither)."""

DISCLAIMER = "Facts-only. No investment advice."

SCHEME_LIST = "HDFC Mid Cap, Small Cap, Large Cap, Multi Cap and Silver ETF FoF"

SUPPORTED_FACTS = ("expense ratio, exit load, minimum SIP or lump sum, benchmark, "
                   "riskometer, fund manager, NAV or fund size")

WELCOME = (f"Hi, I answer factual questions about five HDFC Mutual Fund schemes "
           f"({SCHEME_LIST}) using their Groww pages.")

# Every example must be answerable from the corpus (edge case UI-11 / GAP-40).
EXAMPLE_QUESTIONS = (
    "What is the expense ratio of HDFC Mid Cap Fund?",
    "What is the exit load of HDFC Small Cap Fund?",
    "What is the benchmark of HDFC Large Cap Fund?",
)

GREETING = (f"{WELCOME} You can ask about the {SUPPORTED_FACTS} of any of these schemes. "
            f"{DISCLAIMER}")

# ---------------------------------------------------------------- refusals (§4.3)

REFUSAL = (
    "I can only share factual information about these HDFC mutual fund schemes, so I can't "
    "give investment advice, recommendations or comparisons. "
    "For help with choosing funds, see the investor education resources linked below, or "
    "speak to a SEBI-registered investment adviser."
)
REFUSAL_ADVICE = REFUSAL
REFUSAL_COMPARISON = (
    "I can't compare or rank funds, because that would be investment advice. "
    "I can tell you individual facts for each scheme, such as its expense ratio, exit load "
    "or benchmark, if you ask about one scheme at a time."
)
REFUSAL_PREDICTION = (
    "I can't predict how a fund or its NAV will perform. "
    "I only share verified facts from the scheme pages; the investor education resources "
    "linked below explain how mutual fund risk and returns work."
)
REFUSALS = {"advice": REFUSAL_ADVICE, "comparison": REFUSAL_COMPARISON,
            "prediction": REFUSAL_PREDICTION}

# ---------------------------------------------------------------- performance (§5)


def performance(scheme_name: str) -> str:
    return (f"I can't share, compare or calculate returns. "
            f"You can see the latest performance figures for {scheme_name} on its Groww page "
            f"linked below.")


PERFORMANCE_WHICH_SCHEME = (
    "I can't share, compare or calculate returns, but I can point you to a scheme's page "
    f"with its performance figures. Which scheme do you mean: {SCHEME_LIST}?"
)

# ---------------------------------------------------------------- other non-answers


def not_found(scheme_name: str | None) -> str:
    where = f"for {scheme_name}" if scheme_name else "in the scheme information I have"
    return (f"I couldn't find that {where}. "
            f"I can answer factual questions such as the {SUPPORTED_FACTS}.")


CLARIFY_SCHEME = f"Which scheme do you mean? I cover {SCHEME_LIST}."


def clarify_one_at_a_time(scheme_names: list[str]) -> str:
    listed = ", ".join(scheme_names[:-1]) + f" or {scheme_names[-1]}"
    return (f"I can answer for one scheme at a time, so each answer can point to a single "
            f"source. Which one first: {listed}?")


CLARIFY_UNCLEAR = (f"Could you ask a factual question about one of the schemes? "
                   f"For example: \"{EXAMPLE_QUESTIONS[0]}\"")

OUT_OF_SCOPE = (f"I can only answer questions about five HDFC Mutual Fund schemes: "
                f"{SCHEME_LIST}. Please ask about one of these.")

PII_WARNING = (
    "For your security, please don't share personal details like PAN, Aadhaar, account or "
    "folio numbers, OTPs, email addresses or phone numbers. "
    "I can only answer general factual questions about mutual fund schemes."
)

UNAVAILABLE = (
    "I can't answer right now because the answering service is temporarily unavailable. "
    "Please try again in a moment."
)
