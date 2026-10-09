# Licensing and distribution: owner decision brief (V2.1)

**Status:** no licence has been chosen. The repository has no `LICENSE` file, and the package
manifests declare no licence. Until the owner decides, the code is "all rights reserved" by
default. Nobody else may legally redistribute it, and **no container image or installer
should be published.** This brief separates the six questions the owner must answer and
recommends options. The choice is the owner's.

## 1. Daedelus's own source code

| Option | What it permits | Fits if… | Consequences |
|---|---|---|---|
| **Apache-2.0** (recommended default) | use, modification and redistribution, including commercially and in closed products; explicit patent grant; NOTICE file | broad adoption, contributions from companies, and the option of a separately licensed hosted service | Compatible with every dependency below. GPL Blender stays a separate program (§3). |
| MIT | as Apache-2.0 without the patent grant and NOTICE terms | maximum simplicity | Weaker patent position. |
| MPL-2.0 | file-level copyleft: modified Daedelus files stay open; combining with closed code is allowed | modifications to the core should come back, but closed plug-ins are acceptable | Matches LibreOffice's licence. |
| AGPL-3.0 | strong copyleft that also covers network use: anyone running a modified hosted Daedelus must publish the changes | discouraging closed hosted forks | Narrows corporate adoption. A commercial dual licence would need a CLA from contributors. |
| Proprietary / source-available | owner-defined | a commercial product without open-source obligations | Contributors would need a CLA. Distribution of GPL components still follows §3. |

Decide this first. Every other item below is compatible with the permissive options.

## 2. Dependencies (audited: `docs/DEPENDENCY_AUDIT.md`)

* Python and JS libraries are permissive (MIT, BSD, Apache-2.0, MPL-2.0 for `certifi`).
  `cryptography` (Apache-2.0 OR BSD) is new in V2.1, used only by the hosted handoff
  (`deploy/hosted/handoff.py`, optional `hosted` extra).
* **Obligations when distributing an installer or image:** keep the licence texts and
  notices of bundled packages (MIT and BSD require the copyright notice; Apache-2.0 requires
  the licence text and any NOTICE). A generated `THIRD_PARTY_NOTICES` file is the usual
  answer. It is **not yet produced**: a pre-publication task.
* `pyinstaller` is GPL with a bootloader exception: freezing the desktop sidecar does not
  impose the GPL on Daedelus.
* Build-time base images (Debian/Ubuntu, Node) contain many packages. Their licences travel
  with the images. Keep the distribution's `/usr/share/doc/*/copyright` files; the default
  images do.

## 3. Redistributing images that contain Blender (GPL-2.0-or-later)

`daedelus-worker-blender` copies the official Blender build into the image. Publishing that
image means **distributing Blender**, which the GPL allows on these conditions:

1. **Licence text and notices.** Ship Blender's licence files. The official build includes
   them under `/opt/blender/`; keep them there.
2. **Corresponding source.** Either include the Blender source for the exact version, or
   accompany the image with a written offer, valid for three years, to provide it. The usual
   practice for an unmodified official build is to publish the matching source archive next
   to the image, for example `blender-4.5.14.tar.xz` from download.blender.org, together with
   the version and the build's commit hash.
3. **No added restrictions** on Blender: no terms forbidding users from extracting or
   redistributing it.
4. **Daedelus's own code in the image keeps its own licence.** It talks to Blender only
   through files and a subprocess.
   * The worker script `blender_worker.py`, which runs *inside* Blender and imports `bpy`, is
     the one file that is reasonably treated as a GPL-covered work when distributed.
   * Choosing a GPL-compatible licence for Daedelus (Apache-2.0 is compatible with GPL-3.0;
     MIT is compatible with both GPL-2.0 and GPL-3.0), or licensing that one file
     GPL-2.0-or-later, removes any doubt.
   * **Owner decision needed.**
5. **Trademarks.** "Blender" is a trademark of the Blender Foundation. Do not imply
   endorsement in image names or descriptions.

The same reasoning applies to the poppler (GPL) and git (GPL) binaries in the Office and code
worker images: ship their licence files and offer their source. Distribution packages
satisfy this when the images keep `/usr/share/doc`. LibreOffice is MPL-2.0: ship its licence
and offer its source.

**Until 1–4 are in place, do not push `daedelus-worker-blender` (or the other worker images)
to a public registry.** Private registries used only by the owner do not distribute to third
parties.

## 4. Attribution and source availability for Daedelus itself

Under Apache-2.0 or MIT nothing beyond the licence and notice files is required. Under
AGPL-3.0, a hosted deployment must offer its users the corresponding source of the running
version.

## 5. Sample creative assets

| Set | Licences | Files |
|---|---|---|
| `demo_assets/v11` | CC BY-SA 2.0 (tree photograph: attribution and share-alike required), CC0 (Poly Haven renders) | `ATTRIBUTION.json` |
| `demo_assets/v12` | NOAA data (US public domain), project-authored notes | `ATTRIBUTION.json` |
| `demo_assets/v21` (new) | CC0 (coffee, cat, brick: scikit-image sample data), public domain (SpaceX launch photo), project-authored illustration | `ATTRIBUTION.json` |

The CC BY-SA photograph obliges anyone redistributing *that image* to credit Evelyn Simak and
keep the CC BY-SA licence. It does not affect the code. Replace it with a CC0 image if
share-alike is unwanted in distributions.

## 6. Model provider and API terms

* Anthropic and Google API use is governed by each provider's commercial terms and usage
  policies. Users bring their own keys, and no key is bundled.
* Before offering a hosted service, the owner should check:
  * the providers' terms on serving end users;
  * data retention: some Claude models require 30-day retention and are not available under
    zero data retention unless Anthropic authorises it;
  * output ownership terms;
  * whether user media may be uploaded. Gemini's Files API stores uploads temporarily;
    Daedelus deletes them after the call.
* Microsoft Graph and Google Drive connectors use the user's own OAuth grants. Publishing an
  app that requests restricted Drive scopes needs Google's verification. The recommended
  `drive.file` scope needs only basic verification.

## Recommended path (for the owner to confirm)

1. Choose **Apache-2.0** for the code. If network copyleft is a priority, choose AGPL-3.0
   with a CLA.
2. Add `LICENSE`, `NOTICE`, and a generated `THIRD_PARTY_NOTICES` for the installers and
   images.
3. Publish worker images only with Blender, poppler, git and LibreOffice licence files
   included and the matching source archives (or a written offer) published alongside.
4. Decide whether the CC BY-SA demo photograph stays.
