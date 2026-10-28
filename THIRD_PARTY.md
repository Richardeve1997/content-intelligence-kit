# Third-party software

This file lists the software the kit depends on but does not own, where each piece comes from, and the terms it carries.

Each piece reaches your Mac in one of three ways. Bundled means its files are in this repository. Fetched at setup means a setup command downloads it: `npm ci`, `bin/fetch_gsap.sh`, or `uv sync --group video --group research --inexact`, which installs the Python tools. Downloaded at run time means the kit fetches it the first time a job needs it, into a cache outside this folder. Only the bundled pieces are in this repository.

| Software | Licence | How it arrives |
| --- | --- | --- |
| Inter, Inter Tight and JetBrains Mono fonts | SIL Open Font License 1.1 | bundled in `kit/fonts/` |
| Humanizer skill | MIT, with examples from Wikipedia under CC BY-SA 4.0 | bundled in `.claude/skills/humanizer/` |
| HyperFrames 0.8.68 | Apache-2.0 | fetched at setup by `npm ci` |
| GSAP 3.15.0 | GSAP's standard "no charge" license | fetched at setup by `bin/fetch_gsap.sh` |
| yt-dlp | The Unlicense | fetched at setup with the Python tools |
| mlx-whisper | MIT | fetched at setup with the Python tools, on Apple silicon only; its speech model is downloaded at run time |
| faster-whisper | MIT | fetched at setup with the Python tools; its speech model is downloaded at run time |
| WhisperX | BSD-2-Clause | fetched at setup with the Python tools; its alignment models are downloaded at run time |
| MediaPipe | Apache-2.0 | fetched at setup with the Python tools; its selfie segmentation model is downloaded at run time |
| Robust Video Matting | GPL-3.0 | downloaded at run time by torch.hub, code and weights |

## HyperFrames

HyperFrames renders the kit's HTML and GSAP animations to video. It is licensed under Apache-2.0: https://github.com/heygen-com/hyperframes.

`package.json` pins it at exactly 0.8.68, and `package-lock.json` records the same version. `npm ci` installs it into `node_modules/` during setup, so this repo does not ship it. To move to another version, change `package.json`, `package-lock.json` and this paragraph together.

## GSAP

GSAP drives the animation inside the HyperFrames compositions. The kit uses GSAP 3.15.0. `bin/fetch_gsap.sh` downloads exactly that version from the npm registry into `kit/vendor/` during setup. The script holds the sha512 checksum of that package, so a download that differs by one byte is thrown away, and it checks the version number inside the package before it writes anything. Setting `GSAP_VERSION` fetches another version instead. That download can only be checked against the digest the registry itself publishes, and the script says so. The download is gitignored, so this repo does not redistribute GSAP. Its standard "no charge" license applies: https://gsap.com/standard-license.

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

The skill's patterns come from the Wikipedia page "Signs of AI writing" (https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing), maintained by WikiProject AI Cleanup, and several of its before-and-after examples are taken from that page. Wikipedia's text is available under the Creative Commons Attribution-ShareAlike 4.0 License (CC BY-SA 4.0): https://creativecommons.org/licenses/by-sa/4.0/. The skill names and links the page.

## yt-dlp

Research uses yt-dlp to read the videos of YouTube creators on the watch list and to download their sound for speech to text. It is released under the Unlicense, a public domain dedication: https://github.com/yt-dlp/yt-dlp. Setup installs it with the Python tools.

## Speech to text

Three packages turn speech into text and line the captions up with the words. Setup installs them with the Python tools, and their models download the first time speech is turned into text, from Hugging Face or the PyTorch servers, into their own caches in your home folder.

| Package | Licence | Source |
| --- | --- | --- |
| mlx-whisper | MIT | https://github.com/ml-explore/mlx-examples |
| faster-whisper | MIT | https://github.com/SYSTRAN/faster-whisper |
| WhisperX | BSD-2-Clause | https://github.com/m-bain/whisperX |

On Apple silicon, mlx-whisper runs `mlx-community/whisper-large-v3-turbo`, a conversion of OpenAI's Whisper large-v3-turbo, which OpenAI released under MIT. Elsewhere faster-whisper runs `Systran/faster-whisper-small`, also MIT. Setting `KIT_ASR_MODEL` picks another model, which comes under its own licence.

WhisperX picks an alignment model by the language it hears. For English that is wav2vec 2.0 base 960h through torchaudio, published under MIT. For French, German, Spanish and Italian it is a VoxPopuli model, and for Vietnamese a model from Hugging Face, all under CC BY-NC 4.0, a licence that does not allow commercial use. The kit never runs those five models: for these languages it keeps the speech model's own word times and prints a note saying so. Other languages get a model from Hugging Face under that model's own licence. When no alignment model is available, the kit uses the speech model's own word times instead.

## Matting

The UI story style cuts the creator out of their footage with a matte, which `kit/matte.py` makes in one of two ways.

Robust Video Matting is the default. It is licensed under GPL-3.0: https://github.com/PeterL1n/RobustVideoMatting. The first time a video needs a matte, torch.hub downloads its code at commit `53d74c6826735f01f4406b5ca9075eee27bec094` and the `rvm_mobilenetv3` weights from the repository's v1.0.0 release into torch's own cache in your home folder. The kit runs it from there, and never copies or changes it.

MediaPipe is the fallback, for when torch is missing or that download fails. The `mediapipe` package is licensed under Apache-2.0 (https://github.com/google-ai-edge/mediapipe), and setup installs it with the Python tools. Its selfie segmentation model, version 1 of the float16 `selfie_segmenter.tflite`, is downloaded from Google's model storage the first time it is needed. The kit checks it against the SHA-256 it holds and keeps it in `~/.cache/content-intelligence-kit/models/` (`KIT_MODEL_CACHE` moves it). The model card gives its licence as Apache-2.0.

## Everything else

The other Python packages in `pyproject.toml` and the Node packages behind HyperFrames come from PyPI and npm during setup, each under its own licence. Playwright downloads a Chromium browser at setup, and HyperFrames downloads its own copy of Chrome the first time it renders. ffmpeg, Node, uv and the other tools the machine check names come from Homebrew or their own installers. None of them is stored in this repo.
