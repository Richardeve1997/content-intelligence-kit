# Third-party software

This file lists the software the kit depends on but does not own, where each piece comes from, and the terms it carries.

## HyperFrames

HyperFrames renders the kit's HTML and GSAP animations to video. It is licensed under Apache-2.0: https://github.com/heygen-com/hyperframes.

`package.json` pins it at exactly 0.8.68, and `package-lock.json` records the same version. The pin is provisional, because the ui-story render checks have not yet run against it. Once they pass, or if they force another version, change `package.json` and this paragraph together.

## GSAP

GSAP drives the animation inside the HyperFrames compositions. `bin/fetch_gsap.sh` downloads it from the npm registry into `kit/vendor/` during setup. The download is gitignored, so this repo does not redistribute GSAP. Its standard "no charge" license applies: https://gsap.com/standard-license.

## Fonts

The kit ships three variable fonts in `kit/fonts/`. Each sits in its own folder with the `OFL.txt` it was released with, and all three are licensed under the SIL Open Font License 1.1: https://openfontlicense.org.

| Family | Folder | Weights | Copyright holder |
| --- | --- | --- | --- |
| Inter Tight | `kit/fonts/inter-tight/` | 100 to 900 | The Inter Project Authors |
| Inter | `kit/fonts/inter/` | 100 to 900 | The Inter Project Authors |
| JetBrains Mono | `kit/fonts/jetbrains-mono/` | 100 to 800 | The JetBrains Mono Project Authors |

The files are unmodified copies from the official Google Fonts repository, https://github.com/google/fonts, in the `ofl/` folder, taken at commit `ddf386ba1b9fbc6b2c818f1ddcd9148f54ced94b`. Only the file names changed, to drop the square brackets of the upstream names. `kit/fonts/fonts.json` records each file's upstream path and SHA-256, and a test checks both.

## Humanizer

`.claude/skills/humanizer/` is a copy of the humanizer skill, which rewrites AI-sounding prose so it reads naturally. It is licensed under MIT, copyright 2025 Siqi Chen: https://github.com/blader/humanizer.

The kit ships version 2.11.2 as it came, with its own `LICENSE` and `README.md` beside it. A test checks the three files against their SHA-256, so a change to them has to be deliberate.
