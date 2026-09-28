import html
import re

_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_CODE = re.compile(r"`([^`\n]+)`")
_HEADING = re.compile(r"^(#{1,3}) (.+)$", re.M)
_ORDERED = re.compile(r"^\d{1,3}[.)] ")
_FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)[^\S\n]*\n(.*?)(?:\n```[^\n]*(?:\n|$)|\Z)", re.S)


def render_markdown(source: str) -> str:
    rendered = []
    cursor = 0
    for block in _FENCE.finditer(source):
        rendered.append(_inline(source[cursor : block.start()]))
        language = block.group(1) or "text"
        rendered.append(
            f'<figure class="code"><figcaption>{html.escape(language)}</figcaption>'
            f"<pre><code>{html.escape(block.group(2))}</code></pre></figure>"
        )
        cursor = block.end()
    rendered.append(_inline(source[cursor:]))
    return "".join(rendered)


def _inline(text: str) -> str:
    blocks = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("|") and index + 1 < len(lines) and re.match(r"^\|?[\s:-]+\|", lines[index + 1]):
            rows = [line]
            index += 2
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(lines[index])
                index += 1
            blocks.append(_table(rows))
            continue
        if re.match(r"^#{1,3} ", line):
            level = len(line) - len(line.lstrip("#"))
            body = _marks(html.escape(line[level + 1 :]))
            blocks.append(f"<h{level}>{body}</h{level}>")
        elif line.startswith("- "):
            items = []
            while index < len(lines) and lines[index].startswith("- "):
                items.append(f"<li>{_marks(html.escape(lines[index][2:]))}</li>")
                index += 1
            blocks.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif _ORDERED.match(line):
            items = []
            while index < len(lines) and _ORDERED.match(lines[index]):
                items.append(f"<li>{_marks(html.escape(_ORDERED.sub('', lines[index], count=1)))}</li>")
                index += 1
            blocks.append("<ol>" + "".join(items) + "</ol>")
            continue
        elif line.strip() == "":
            pass
        else:
            blocks.append(f"<p>{_marks(html.escape(line))}</p>")
        index += 1
    return "".join(blocks)


def _marks(escaped: str) -> str:
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = _CODE.sub(r"<code>\1</code>", escaped)
    return _LINK.sub(
        r'<a href="\2" rel="noopener noreferrer">\1</a>',
        escaped,
    )


def _table(rows: list[str]) -> str:
    def cells(row: str) -> list[str]:
        return [cell.strip() for cell in row.strip("|").split("|")]

    head = "".join(f"<th>{_marks(html.escape(cell))}</th>" for cell in cells(rows[0]))
    body = []
    for row in rows[1:]:
        body.append("<tr>" + "".join(f"<td>{_marks(html.escape(cell))}</td>" for cell in cells(row)) + "</tr>")
    return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
