"""Static guard for LLM-generated SQL.

Defence in depth: this is the first layer; the executor additionally runs
inside a READ ONLY transaction with a statement_timeout. No third-party SQL
parser: a small tokenizer strips strings/comments, then rules are checked
on the remaining code.
"""

from __future__ import annotations

import re

from agents.ask.schema import ALLOWED_TABLES

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

FORBIDDEN_KEYWORDS = frozenset({
    "insert", "update", "delete", "merge", "upsert", "drop", "create", "alter",
    "truncate", "grant", "revoke", "copy", "vacuum", "analyze", "refresh",
    "call", "do", "execute", "prepare", "listen", "notify", "lock", "set",
    "reset", "comment", "reindex", "cluster", "discard", "into", "begin",
    "commit", "rollback", "savepoint",
})
FORBIDDEN_FUNCTIONS = re.compile(
    # Any pg_* admin/file/sleep function, any *xml* helper (query_to_xml runs
    # SQL hidden in a string and skips the table allowlist), plus the rest.
    r"\b(pg_\w+|\w*xml\w*|xpath\w*|lo_\w+|dblink\w*|set_config|current_setting|"
    r"txid_\w+|nextval|setval|currval|version|inet_\w+|current_database|"
    r"current_schemas?|has_\w+_privilege)\s*\(",
    re.IGNORECASE,
)
# Catalog schemas are never needed by /ask questions.
FORBIDDEN_SCHEMAS = re.compile(r"\b(pg_catalog|information_schema|pg_toast)\s*\.", re.IGNORECASE)
# A string literal that itself looks like SQL is never a legitimate filter value.
SQL_IN_LITERAL = re.compile(
    r"\b(select|insert|update|delete|drop|union|copy|create|alter|grant)\b\s+\S", re.IGNORECASE)


class SQLGuardError(ValueError):
    """Raised when SQL fails a guard rule. The message is safe to show/feed back."""


def _strip_literals(sql: str) -> str:
    """Replace string literals, quoted identifiers and comments with placeholders."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            if j == -1:
                raise SQLGuardError("Μη κλειστό σχόλιο /* */")
            i = j + 2
            out.append(" ")
        elif c == "'":
            prev = sql[max(0, i - 2):i]
            if re.search(r"(^|[^\w])[eEbBxXnN]$", " " + prev) or prev.endswith("&"):
                raise SQLGuardError("Ειδικά string (E'', U&'', B'', X'') δεν επιτρέπονται")
            j = i + 1
            while True:
                j = sql.find("'", j)
                if j == -1:
                    raise SQLGuardError("Μη κλειστό string")
                if sql.startswith("''", j):
                    j += 2
                    continue
                break
            if SQL_IN_LITERAL.search(sql[i + 1:j]):
                raise SQLGuardError("Το string μοιάζει με SQL")
            i = j + 1
            out.append("''")
        elif c == '"':
            j = sql.find('"', i + 1)
            if j == -1:
                raise SQLGuardError("Μη κλειστό quoted identifier")
            out.append(sql[i:j + 1].lower().replace('"', ""))
            i = j + 1
        elif c == "$" and re.match(r"\$\w*\$", sql[i:]):
            raise SQLGuardError("Dollar-quoted strings δεν επιτρέπονται")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _referenced_relations(code: str) -> set[str]:
    rels = set()
    for m in re.finditer(r"\b(?:from|join)\s+([a-z_][\w.]*)", code, re.IGNORECASE):
        rels.add(m.group(1).lower())
    # comma joins: FROM a x, b y
    for m in re.finditer(r"\bfrom\s+(.+?)(?:\bwhere\b|\bgroup\b|\border\b|\blimit\b|\bjoin\b|\)|$)",
                         code, re.IGNORECASE | re.DOTALL):
        for part in m.group(1).split(",")[1:]:
            tok = part.strip().split()
            if tok and re.match(r"[a-z_][\w.]*$", tok[0], re.IGNORECASE):
                rels.add(tok[0].lower())
    return rels


def _cte_names(code: str) -> set[str]:
    return {m.group(1).lower() for m in
            re.finditer(r"(?:\bwith\b|,)\s*(?:recursive\s+)?([a-z_]\w*)\s+as\s*\(", code, re.IGNORECASE)}


def validate_sql(sql: str, allowed: frozenset[str] = ALLOWED_TABLES,
                 default_limit: int = DEFAULT_LIMIT, max_limit: int = MAX_LIMIT) -> str:
    """Return a safe, LIMIT-bounded version of `sql` or raise SQLGuardError."""
    if not sql or not sql.strip():
        raise SQLGuardError("Κενό SQL")
    sql = sql.strip().rstrip(";").strip()
    code = _strip_literals(sql)
    if ";" in code:
        raise SQLGuardError("Επιτρέπεται μόνο ένα statement")
    first = re.match(r"\s*(\w+)", code)
    if not first or first.group(1).lower() not in ("select", "with"):
        raise SQLGuardError("Επιτρέπεται μόνο SELECT")
    words = {w.lower() for w in re.findall(r"[a-z_]+", code, re.IGNORECASE)}
    bad = sorted(words & FORBIDDEN_KEYWORDS)
    if bad:
        raise SQLGuardError(f"Απαγορευμένη λέξη-κλειδί: {', '.join(bad)}")
    if FORBIDDEN_SCHEMAS.search(code):
        raise SQLGuardError("Μη επιτρεπτό schema")
    if FORBIDDEN_FUNCTIONS.search(code):
        raise SQLGuardError("Απαγορευμένη συνάρτηση")
    ctes = _cte_names(code)
    for rel in _referenced_relations(code):
        name = rel.split(".")[-1]
        if "." in rel and rel.split(".")[0] != "public":
            raise SQLGuardError(f"Μη επιτρεπτό schema: {rel}")
        if name not in allowed and name not in ctes:
            raise SQLGuardError(f"Μη επιτρεπτός πίνακας: {name}")

    # LIMIT: enforce on the outermost query (trailing LIMIT n [OFFSET m]).
    m = re.search(r"\blimit\s+(\d+|all)\s*(\boffset\s+\d+\s*)?$", code, re.IGNORECASE)
    if m:
        val = m.group(1).lower()
        if val == "all" or int(val) > max_limit:
            start = m.start(1)
            sql = sql[:start] + str(max_limit) + sql[m.end(1):]
    else:
        sql = f"{sql}\nLIMIT {default_limit}"
    return sql
