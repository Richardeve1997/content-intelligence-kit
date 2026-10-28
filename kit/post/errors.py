"""Errors the posting code raises. Each one carries a plain sentence the creator can act on."""

from __future__ import annotations

from kit.config import KitError

__all__ = ["PostError", "CaptionProblems"]


class PostError(KitError):
    """Something stops a video from being posted: a missing file, a bad name or time, or a damaged record."""


class CaptionProblems(PostError):
    """The captions break a rule. `problems` holds one sentence for each, and nothing was sent."""

    def __init__(self, problems):
        self.problems = list(problems)
        count = len(self.problems)
        head = "A caption breaks a rule:" if count == 1 else f"{count} caption rules are broken:"
        lines = "\n".join(f"- {problem}" for problem in self.problems)
        super().__init__(f"{head}\n{lines}\nFix them in captions.json, show the creator the new words, and ask again.")

    def __reduce__(self):
        return (CaptionProblems, (self.problems,))
