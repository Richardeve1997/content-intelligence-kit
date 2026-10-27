# Third-party software

This file lists the software the kit depends on but does not own, where each piece comes from, and the terms it carries.

## HyperFrames

HyperFrames renders the kit's HTML and GSAP animations to video. It is licensed under Apache-2.0: https://github.com/heygen-com/hyperframes.

`package.json` pins it at exactly 0.8.68, and `package-lock.json` records the same version. The pin is provisional, because the ui-story render checks have not yet run against it. Once they pass, or if they force another version, change `package.json` and this paragraph together.

## GSAP

GSAP drives the animation inside the HyperFrames compositions. `bin/fetch_gsap.sh` downloads it from the npm registry into `kit/vendor/` during setup. The download is gitignored, so this repo does not redistribute GSAP. Its standard "no charge" license applies: https://gsap.com/standard-license.
