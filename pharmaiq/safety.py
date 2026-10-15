"""
Safety boundary for PharmaIQ.

PharmaIQ is a REFERENCE and ANALYTICS tool. It must never:
  * recommend patient-specific doses,
  * diagnose or recommend a treatment for a person,
  * reveal credentials or other users' data.

check_question() is a deterministic, rule-based pre-filter that runs BEFORE any LLM
sees the question, so the boundary cannot be talked around by prompt injection.
"""
import re
from dataclasses import dataclass

CLINICAL_REFUSAL = (
    "I can't give personal medical advice — that includes doses for a specific person, whether a "
    "medicine is safe for someone, diagnosing symptoms, or recommending a treatment. "
    "Please ask the dispensing pharmacist or the prescriber.\n\n"
    "What I can do: show what an approved leaflet or SOP says about a medicine "
    "(for example “What are the warnings for warfarin?”), check stock, expiry and sales data."
)

CREDENTIAL_REFUSAL = (
    "I can't share passwords, API keys, secrets or other users' account details. "
    "I can help with inventory, sales and documentation questions instead."
)

_PERSON = re.compile(
    r"\b(my|his|her|their|our)\s+(child|children|kid|kids|son|daughter|baby|infant|toddler|newborn|mother|mom|"
    r"mum|father|dad|wife|husband|patient|grandmother|grandfather|grandma|grandpa|partner|friend|dog|cat)\b")
_DOSE = re.compile(
    r"\b(dose|dosage|dosing|how much|how many|how often|give|giving|take|taking|safe|safely|administer|"
    r"mg|ml|tablets?|pills?|capsules?|drops?|treat|treatment|cure|medicine for|help with)\b")
_CAN_I = re.compile(
    r"\b(can|should|may|could|must)\s+(i|he|she)\s+(take|give|use|mix|combine|drink|stop|skip|double)\b"
    r"(?!\s+(a\s+look|a\s+moment|a\s+break|a\s+quick|the\s+day|over))")
# a "can I take X" question is only clinical when a medicine-like cue is present
_MED_CUE = re.compile(
    r"\b(with|together|alongside|at the same time|mg|tablets?|pills?|capsules?|medicine|medication|drugs?|dose|"
    r"antibiotic|painkiller|syrup|inhaler|insulin|aspirin|\w{4,}(ol|in|ine|ide|ate|one|cillin|mycin|azole|pril|"
    r"sartan|statin|formin|profen|xen))\b")
_PREG = re.compile(r"\b(pregnan\w*|breast-?feeding|breastfeed\w*|nursing mother|lactating)\b")
_PREG_Q = re.compile(r"\b(safe|take|taking|use|using|can|okay|ok|allowed|avoid|dose)\b")
_RECOMMEND = re.compile(
    r"\b(what|which)\s+(medicine|medication|drug|tablet|pill|antibiotic|painkiller|syrup)s?\s+"
    r"(should|can|do|would|is best|are best)\s+(i|we|you)\b"
    r"|\brecommend\s+(me\s+)?(a|an|some|the best)?\s*(medicine|medication|drug|tablet|pill|antibiotic|painkiller)\b"
    r"|\bwhat\s+should\s+i\s+(take|give|use)\b"
    r"|\b(prescribe|diagnos\w*)\b"
    r"|\bwhat('s| is)\s+wrong\s+with\s+(me|him|her|my)\b"
    r"|\bdo\s+i\s+have\s+(a\s+)?(infection|diabetes|cancer|covid|flu|asthma|allergy|condition|disease)\b"
    r"|\bis\s+it\s+safe\s+(for|to)\b.*\b(take|give|use|combine|mix)\b")
_PERSONAL_NUMBERS = re.compile(r"\b\d+(\.\d+)?\s*(kg|kilos?|kilograms?|lbs?|pounds?|years?\s*old|yrs?\s*old|yo|months?\s*old|weeks?\s*old)\b")
_CREDENTIALS = re.compile(
    r"\b(password|passwd|api[\s_-]?key|secret[\s_-]?key|secret_key|token|credentials?|hash(ed)?\s+password|"
    r"users?\s+table|show\s+(me\s+)?all\s+users)\b")


@dataclass
class Refusal:
    code: str
    message: str


def check_question(question: str, role: str = "staff"):
    """Return a Refusal if the question crosses the safety boundary, else None."""
    q = " ".join((question or "").lower().split())
    if _CREDENTIALS.search(q):
        return Refusal("credentials", CREDENTIAL_REFUSAL)
    clinical = False
    if _PERSON.search(q) and _DOSE.search(q):
        clinical = True
    elif _CAN_I.search(q) and _MED_CUE.search(q):
        clinical = True
    elif _PREG.search(q) and _PREG_Q.search(q):
        clinical = True
    elif _RECOMMEND.search(q):
        clinical = True
    elif _PERSONAL_NUMBERS.search(q) and _DOSE.search(q):
        clinical = True
    if clinical:
        return Refusal("clinical_advice", CLINICAL_REFUSAL)
    return None


DISCLAIMER = ("Reference information only — not individualised medical advice. "
              "Always confirm with a pharmacist or the approved prescribing information.")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_DIGITS = re.compile(r"(?<!\d)(?:\+?\d[\s-]?){7,}(?!\d)")


def redact(text: str, limit: int = 300) -> str:
    """Strip emails / phone-like numbers before a question is written to the audit log."""
    text = _EMAIL.sub("[email]", text or "")
    text = _LONG_DIGITS.sub("[number]", text)
    return text[:limit]
