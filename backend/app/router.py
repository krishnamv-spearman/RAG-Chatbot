"""Intent router: decides what kind of reply a message gets, and which scheme it's about.

Rule priority (edge case GAP-10): PII (handled earlier by the guard) > unclear >
greeting/help > advice/comparison/prediction > performance > out of scope > clarify >
factual. Template replies (refusal, performance, greeting, out of scope, clarify) need
no retrieval and no LLM call. Factual questions go on to retrieval + generation, where the
LLM's own `refusal` kind and the validator's advice check are the second and third lines
of defence against advice slipping through.

Advice is detected by PHRASES, not single words: "better" or "vs" alone also appear in
factual questions (edge case ADV-08).
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from app.registry import Scheme

Intent = Literal["greeting", "unclear", "advisory", "performance", "out_of_scope",
                 "clarify", "factual"]
RefusalVariant = Literal["advice", "comparison", "prediction"]
ClarifyReason = Literal["multiple_schemes", "no_scheme", "performance_no_scheme", "unclear"]


@dataclass
class ConversationContext:
    """What the router needs from the thread (Phase 5 stores it per thread)."""

    last_scheme: str | None = None
    history: list[tuple[str, str]] = field(default_factory=list)  # (role, text), oldest first

    def last_user_question(self) -> str | None:
        return next((t for r, t in reversed(self.history) if r == "user"), None)


@dataclass
class Route:
    intent: Intent
    scheme: str | None = None  # resolved scheme (from the message, else the thread)
    schemes_mentioned: list[str] = field(default_factory=list)
    variant: RefusalVariant | None = None
    clarify: ClarifyReason | None = None
    from_context: bool = False  # scheme came from the thread, not the message
    needs_rewrite: bool = False  # elliptical follow-up ("and small cap?")
    has_fact: bool = False  # the message names a fact (expense ratio, exit load, ...)
    reason: str = ""


def normalize(text: str) -> str:
    text = text.lower().replace("&", " and ").replace("’", "'")
    text = re.sub(r"[-_/]", " ", text)
    text = re.sub(r"[^\w\s'%₹.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compile(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


# ---------------------------------------------------------------- intent patterns
# Matched against normalize(text): lowercase, hyphens -> spaces, "&" -> "and".

_GREETING = re.compile(
    r"(?:hi+|hello+|hey+|hii+|namaste|good (?:morning|afternoon|evening)|thanks?(?: you)?|"
    r"thank you(?: so much)?|thx|ok(?:ay)?|cool|great|bye|goodbye)[\s.!]*")
_HELP = _compile([
    r"\bwhat can you (?:do|answer|help)", r"^help\b", r"\bhow (?:does|do) (?:this|you) work",
    r"\bwho are you\b", r"\bwhat do you (?:do|know)\b", r"\bwhat (?:can|should) i ask\b",
])

_PREDICTION = _compile([
    r"\bwill (?:it|this|the|its|nav|price|silver|gold|market|returns?|fund|hdfc|mid|small|"
    r"large|multi)\b.{0,40}\b(?:go up|go down|rise|fall|increase|decrease|grow|crash|"
    r"double|recover|perform|beat|give|return|be higher|be lower)\b",
    r"\b(?:predict|prediction|forecast|projection|outlook)\b",
    r"\bfuture (?:returns?|performance|nav|price|value)\b",
    r"\bexpected (?:returns?|growth|nav)\b",
    r"\b(?:next|coming) (?:year|month|week|quarter)\b.{0,30}\b(?:returns?|nav|price|perform)",
    r"\b(?:going|likely) to (?:go up|go down|rise|fall|increase|decrease|crash|grow)\b",
    r"\bwhat will (?:be )?(?:the |its )?(?:nav|price|returns?|value)\b",
    r"\b(?:nav|price|returns?|value)\b.{0,40}\b(?:next|coming) (?:year|month|week|quarter)\b",
])

_COMPARISON = _compile([
    r"\bwhich (?:one |fund |scheme |of (?:these|them|the \w+) )?(?:is |are )?(?:better|best|"
    r"good|safer|riskier|superior|preferable|cheaper)\b",
    r"\bbetter than\b", r"\bworse than\b", r"\b(?:is|are) .{1,40} better\b(?! known)",
    r"\bcompare\b", r"\bcomparison\b", r"\bcompared to\b",
    r"\brank(?:ing)?\b.{0,20}\b(?:funds?|schemes?)\b", r"\b(?:top|best) (?:\d+ )?(?:funds?|"
    r"schemes?|hdfc)\b",
    r"\bwhich (?:fund|scheme|one) should\b", r"\bor\b.{1,40}\bwhich\b",
])
# "vs" / "versus" / "difference between" are comparisons only between schemes
# (checked in code: >= 2 schemes or category words), so "TRI vs PRI" stays factual.
_VS = re.compile(r"\bvs\.?\b|\bversus\b|\bdifference between\b")

_ADVICE = _compile([
    r"\bshould i\b", r"\bshall i\b", r"\bdo you (?:think|recommend|suggest)\b",
    r"\bis (?:it|this|that|now)(?: fund| scheme)?(?: the| a| an)? (?:good|safe|worth|wise|"
    r"right|bad|risky|ok|okay|smart|profitable)\b",
    r"\bright time to (?:invest|buy|sell|enter|exit|redeem)\b",
    # Judgement questions: "Is 0.74% expense ratio good?", "Is the exit load fair?". "High"
    # and "low" are left out: "Is the riskometer Very High?" is a factual question.
    r"^(?:is|are|was|isn't|aren't) .{0,60}\b(?:good|bad|safe|worth|expensive|cheap|"
    r"reasonable|fair|attractive|decent|okay|ok)\b(?! known)",
    r"\b(?:is|are) (?:hdfc|the|this|these)\b.{0,40}\b(?:good|safe|worth it|worth investing|"
    r"a good (?:fund|investment|option|choice|buy)|bad|profitable)\b",
    r"\bgood (?:fund|investment|option|choice|time to (?:invest|buy))\b",
    r"\bworth (?:investing|buying|it)\b", r"\brecommend", r"\bsuggest", r"\badvi[cs]e\b",
    r"\badvis[eo]r\b", r"\bguide me\b",
    r"\b(?:where|which fund|what fund|which scheme) (?:should|to|do|can) (?:i )?(?:invest|put|"
    r"park|buy)\b", r"\bwhere to invest\b", r"\bhow much (?:should|to) (?:i )?invest\b",
    r"\b(?:suitable|right|ideal|good) for (?:me|my|someone|a beginner|beginners|retirement|"
    r"long term)\b", r"\bwho should (?:invest|buy)\b",
    r"\b(?:save|saving|reduce|minimi[sz]e) (?:on )?tax\b", r"\btax saving\b",
    r"\bpretend\b", r"\bact as\b", r"\brole ?play\b", r"\bmy (?:portfolio|financial|goal)\b",
    r"\bi am \d+\b", r"\bi(?:'m| am) (?:a )?(?:\d+|beginner|new investor|retired|young)\b",
    r"\bi have (?:₹|rs\.?|inr)?\s?\d", r"\bbuy or sell\b", r"\bhold or sell\b",
    r"\b(?:invest|investing) (?:now|today|in this market)\b", r"\bopinion\b",
    r"\bwhat do you think\b", r"\byes or no\b", r"\bsafe to invest\b", r"\bis it safe\b",
])

_PERFORMANCE = _compile([
    r"\breturns?\b(?! form)", r"\bcagr\b", r"\bxirr\b", r"\bperform(?:ance|ed|ing|s)?\b",
    r"\bhow much (?:will|would|can|could|do|did) (?:i|my money|it)\b.{0,30}\b(?:get|earn|make|"
    r"grow|become|be worth)\b",
    r"\b(?:profit|gains?)\b", r"\bsharpe\b", r"\bsortino\b", r"\balpha\b",
    r"\bbeta\b", r"\bstandard deviation\b", r"\bvolatility\b", r"\bdrawdown\b",
    r"\bnav (?:history|trend|growth|change|movement|chart|graph)\b",
    r"\bhistorical nav\b", r"\bnav (?:in|of|for|over|during) (?:the )?(?:last|past|previous)\b",
    r"\b(?:\d+|one|three|five|ten) ?(?:y|yr|yrs|year|years)\b.{0,15}\b(?:returns?|"
    r"growth|cagr|performance)\b",
    r"\b(?:if|when) i invest(?:ed)?\b.{0,50}\b(?:years?|months?)\b",
    r"\bsip calculator\b", r"\bgrowth of\b.{0,20}\binvestment\b",
])
_CAPITAL_GAINS = re.compile(r"\bcapital gains?\b")

# Other AMCs: a name followed by fund-ish words, so "ICICI Bank" (a holding) is not one.
_OTHER_AMC = re.compile(
    r"\b(?:sbi|icici(?: prudential)?|axis|nippon(?: india)?|kotak(?: mahindra)?|"
    r"mirae(?: asset)?|parag parikh|ppfas|uti|aditya birla(?: sun life)?|absl|dsp|tata|quant|"
    r"motilal(?: oswal)?|franklin(?: templeton)?|invesco(?: india)?|edelweiss|canara robeco|"
    r"bandhan|hsbc|sundaram|bajaj finserv|lic|union|baroda bnp(?: paribas)?|pgim(?: india)?|"
    r"jm financial|navi|whiteoak|samco|360 one|quantum|mahindra manulife|helios|zerodha)\b"
    r"(?=.{0,40}\b(?:fund|funds|cap|scheme|elss|etf|fof|mf|index|flexi|bluechip|"
    r"tax saver|hybrid|debt|liquid)\b)")

# HDFC schemes outside the corpus (edge case SCH-04 / SCH-10): the scheme word must follow
# "hdfc" closely ("HDFC Flexi Cap", "HDFC Nifty 50 Index Fund"), so a question like
# "benchmark index of the HDFC fund" isn't caught.
_UNSUPPORTED_HDFC = re.compile(
    r"\bhdfc (?:\w+ ){0,2}(?:large and mid ?cap|large mid ?cap|flexi ?cap|elss|tax ?saver|"
    r"balanced advantage|"
    r"liquid|overnight|money market|arbitrage|equity savings|hybrid|focused|focus 30|value|"
    r"dividend yield|infrastructure|pharma|healthcare|banking|financial services|"
    r"technology|consumption|manufacturing|defence|transportation|business cycle|"
    r"children'?s?|retirement|index|nifty|sensex|gold|corporate bond|gilt|short term|"
    r"credit risk|dynamic debt|income fund|ultra short|low duration|banking and psu|"
    r"capital builder|top 200|prudence|non cyclical|mnc|innovation|housing)\b")
# "large and mid cap" must not resolve to Large Cap or Mid Cap.
_CATEGORY_COMBO = re.compile(r"\blarge (?:and )?mid ?cap\b")

# Fact words: used to tell "and its exit load?" (has a fact) from "and small cap?" (no fact).
_FACT_TERMS = _compile([
    r"\bexpense", r"\bter\b", r"\bfees?\b", r"\bcharges?\b", r"\bcost\b", r"\bexit\b",
    r"\bload\b", r"\bredeem", r"\bredemption", r"\bsip\b", r"\blump ?sum", r"\bminimum\b",
    r"\bmin\b", r"\bamount\b", r"\bbenchmark", r"\bindex\b", r"\brisk", r"\bmanag",
    r"\baum\b", r"\bsize\b", r"\bassets\b", r"\bnav\b", r"\bhold", r"\bstocks?\b",
    r"\bportfolio\b", r"\bisin\b", r"\blaunch", r"\binception\b", r"\bregistrar\b", r"\brta\b",
    r"\bstamp\b", r"\bobjective\b", r"\binvests? in\b", r"\bcategory\b", r"\btype\b",
    r"\bplan\b", r"\boption\b", r"\block", r"\btax", r"\bdetails?\b", r"\binfo",
    r"\bfund house\b", r"\bamc\b", r"\bstarted\b", r"\bage\b", r"\bold\b",
])
_FOLLOW_UP = _compile([
    r"^(?:and|also|plus|what about|how about|same for|and for|for|ok and|then)\b",
    r"\b(?:it|its|it's|this fund|that fund|the fund|this scheme|that scheme|this one|"
    r"that one|same fund|same scheme)\b",
])


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Router:
    def __init__(self, schemes: dict[str, Scheme]):
        self.schemes = schemes
        terms: dict[str, str] = {}
        for sid, s in schemes.items():
            for alias in (*s.aliases, s.name):
                terms[normalize(alias)] = sid
        # Longest first, so "silver etf fof" wins over "silver".
        self._terms = sorted(terms.items(), key=lambda kv: len(kv[0]), reverse=True)

    # ------------------------------------------------------------ schemes

    def find_schemes(self, text: str) -> list[str]:
        """Schemes mentioned in the message, in order of appearance, exact aliases first
        and then typo-tolerant matching (edge case IN-06 / GAP-2)."""
        norm = _CATEGORY_COMBO.sub(" ", normalize(text))
        found: list[tuple[int, str]] = []
        taken = [False] * len(norm)
        for term, sid in self._terms:
            for m in re.finditer(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", norm):
                if not any(taken[m.start():m.end()]):
                    taken[m.start():m.end()] = [True] * (m.end() - m.start())
                    found.append((m.start(), sid))
        if not found:
            found = self._fuzzy(norm)
        ordered = [sid for _, sid in sorted(found)]
        return list(dict.fromkeys(ordered))  # unique, keep order

    def _fuzzy(self, norm: str) -> list[tuple[int, str]]:
        words = norm.split()
        hits = []
        for term, sid in self._terms:
            n = len(term.split())
            if n < 2 or len(term) < 7:  # only multi-word aliases ("mid cap"), not "silver"
                continue
            for i in range(len(words) - n + 1):
                window = " ".join(words[i:i + n])
                if window[0] == term[0] and _levenshtein(window, term) <= 2:
                    hits.append((len(" ".join(words[:i])), sid))
        return hits

    # ------------------------------------------------------------ intent

    def route(self, text: str, context: ConversationContext | None = None) -> Route:
        context = context or ConversationContext()
        norm = normalize(text)
        if not re.search(r"[a-z]", norm):
            return Route("unclear", clarify="unclear", reason="no words")

        other_amc = bool(_OTHER_AMC.search(norm))
        only_other_amc = other_amc and "hdfc" not in norm
        # "SBI Small Cap" must not resolve to HDFC Small Cap (edge case SCH-05).
        mentioned = [] if only_other_amc else self.find_schemes(text)
        scheme = mentioned[0] if len(mentioned) == 1 else None
        from_context = False
        if not mentioned and context.last_scheme and not only_other_amc:
            scheme, from_context = context.last_scheme, True
        route = Route("factual", scheme=scheme, schemes_mentioned=mentioned,
                      from_context=from_context)

        if _GREETING.fullmatch(norm) or _HELP.search(norm):
            route.intent, route.reason = "greeting", "greeting/help"
            return route
        if _PREDICTION.search(norm):
            return self._refuse(route, "prediction")
        if _COMPARISON.search(norm) or (_VS.search(norm) and (len(mentioned) >= 2 or other_amc)):
            return self._refuse(route, "comparison")
        if _ADVICE.search(norm):
            return self._refuse(route, "advice")
        if only_other_amc:
            route.intent, route.reason = "out_of_scope", "other AMC"
            return route
        if _PERFORMANCE.search(_CAPITAL_GAINS.sub(" ", norm)):
            route.intent, route.reason = "performance", "returns/performance"
            if route.scheme is None:
                route.intent, route.clarify = "clarify", "performance_no_scheme"
            return route
        if not mentioned and _UNSUPPORTED_HDFC.search(norm):
            route.intent, route.reason = "out_of_scope", "unsupported HDFC scheme"
            route.scheme = None
            return route
        if _CATEGORY_COMBO.search(norm) and not mentioned:
            route.intent, route.reason, route.scheme = "out_of_scope", "large and mid cap", None
            return route
        if len(mentioned) >= 2:
            route.intent, route.clarify = "clarify", "multiple_schemes"
            route.reason = "two or more schemes in one question"
            return route

        has_fact = bool(_FACT_TERMS.search(norm))
        route.has_fact = has_fact
        follow_up = bool(_FOLLOW_UP.search(norm))
        if not mentioned and not context.last_scheme and follow_up and not has_fact:
            route.intent, route.clarify = "clarify", "no_scheme"
            return route
        if not mentioned and not context.last_scheme and follow_up and \
                re.search(r"\b(?:it|its|this fund|that fund|this scheme|that scheme)\b", norm):
            # "and its exit load?" as the first message: no scheme to refer to (FU-08).
            route.intent, route.clarify = "clarify", "no_scheme"
            return route
        # Elliptical follow-up: "and small cap?" / "what about it?" -> needs the previous
        # question to know which fact is meant. Only when a scheme is in focus (named here,
        # or carried by the thread): otherwise a question that merely names no fact
        # ("what is <unknown word>?") would be rewritten into the previous question.
        route.needs_rewrite = (not has_fact and route.scheme is not None
                               and context.last_user_question() is not None)
        route.reason = "factual"
        return route

    @staticmethod
    def _refuse(route: Route, variant: RefusalVariant) -> Route:
        route.intent, route.variant, route.reason = "advisory", variant, f"refusal: {variant}"
        return route
