"""Count application source with a transparent, non-GUI-generated code denominator."""

import io
import json
import tokenize
from pathlib import Path


def measure(root):
    """Exclude blank/comment-only/docstring-only lines, tests and build outputs."""
    files = []
    for path in sorted((root / "src" / "campus_market").glob("*.py")):
        content = path.read_text(encoding="utf-8")
        meaningful = set()
        for token in tokenize.generate_tokens(io.StringIO(content).readline):
            if token.type in (
                tokenize.COMMENT,
                tokenize.STRING,
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENDMARKER,
            ):
                continue
            meaningful.update(range(token.start[0], token.end[0] + 1))
        files.append(
            {
                "file": str(path.relative_to(root)),
                "physical_lines": len(content.splitlines()),
                "nonblank_noncomment_lines": sum(
                    bool(line.strip()) and not line.lstrip().startswith("#")
                    for line in content.splitlines()
                ),
                "token_bearing_lines_excluding_all_string_tokens": len(meaningful),
            }
        )
    return {
        "scope": "application src/campus_market/*.py only; no GUI-generated code",
        "files": files,
        "physical_total": sum(item["physical_lines"] for item in files),
        "nonblank_noncomment_total": sum(item["nonblank_noncomment_lines"] for item in files),
        "conservative_token_line_total": sum(
            item["token_bearing_lines_excluding_all_string_tokens"] for item in files
        ),
    }


if __name__ == "__main__":
    print(json.dumps(measure(Path(__file__).resolve().parents[1]), ensure_ascii=False, indent=2))
