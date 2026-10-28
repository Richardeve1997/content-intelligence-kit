"""The mini me character: from photos or a description to a packed pixel character.

The steps, in the order the `character` skill runs them:

  sheet   the image model draws a character sheet; it is snapped onto a pixel grid and its palette is
          locked once the creator approves it                                    (kit.character.sheet)
  poses   the core poses, drawn in small batches against the approved sheet, snapped onto the locked
          palette and stood on one shared canvas; each can be approved or redrawn (kit.character.poses)
  faces   mouth and blink states, measured on the creator's own face             (kit.character.faces)
  moves   in-between drawings for talking and the three points                  (kit.character.moves)
  pack    strips, face patches and the manifest the HyperFrames runtime reads    (kit.character.pack)

The image model is one of four tools in kit.character.providers, and every prompt is a template in
kit/character/prompts/. Everything a step writes stays in the creator's me/character/ folder.

`kit.character.pack(folder)` is the packing step itself, so callers can write kit.character.pack(path).
"""

from kit.character.pack import pack

__all__ = ["pack"]
