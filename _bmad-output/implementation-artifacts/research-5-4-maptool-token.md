# Research: MapTool (RPTools) token import — epic 5-4 (MapTool target)

Research-first leg (owner directive 2026-09-08: no format shapes from memory). Run by scout `MapToolFormatResearch`, 2026-09-14. All claims carry source URLs (GitHub `RPTools/maptool`, branch **`develop`** — the repo's default branch; stable tag `1.18.6`); anything not confirmable from public sources is marked **UNVERIFIED**.

> **Corrections to the task brief up front.** (1) MapTool is licensed **AGPL-3.0**, not Apache-2.0 ([LICENSE](https://github.com/RPTools/maptool/blob/develop/LICENSE); header on every source file). (2) The stable client is **MapTool 1.18.6** (released 2025-10-30), not a 1.1x — see §2. (3) There is **no standalone JSON token file** MapTool imports ([PersistenceUtil](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java): `loadToken` only reads a `PackedFile`); the only JSON-based persistence is a *campaign-level* "game data" store (DataStoreDto / `data/game-data.json`) that is add-on–scoped and never the payload of a downloaded token — §5 note.

---

## 1. Recommended target surface

**Target = produce a `.rptok` token file (a ZIP) with the portrait PNG embedded as both the token image and portrait, the 5e stat block + race/type/alignment as free text in the token's `notes`, and GM-facing lore in `gmNotes`; the DM imports by DRAG-AND-DROPPING the file onto an open map.**

This is the only surface that exists for a single-NPC download-and-import: MapTool is a sheet-agnostic VTT and its import mechanism for token *files* is drag-drop of a `.rptok` onto the map. Reasons the alternatives are rejected:

- **Stat-block text paste (like FG's Import Text): does not exist.** MapTool has no stat-block text importer. The wiki's only documented ways to get a token on a map are *drag from the Resource Library* or *drag a file/image directly from the file system onto the map* ([Introduction to Tokens](https://wiki.rptools.info/index.php/Introduction_to_Tokens); "you can also drag an image directly from your file system into your map view in MapTool and it will create a token out of it").
- **A `.json` token file: does not exist as an importable format** (see §2 and §5). MapTool's only token-file loader is `PersistenceUtil.loadToken(File)` which opens a ZIP `PackedFile` ([PackedFile.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/lib/io/PackedFile.java), LPUB 24–52: `PROPERTY_FILE = "properties.xml"`, `CONTENT_FILE = "content.xml"`; javadoc: "The implementation uses … a single actual file", i.e. a ZIP). Drops of any `.rptok`-named file are routed to `PersistenceUtil.loadToken`, and non-`.rptok`/image dropped files are rejected as unsupported ([TransferableHelper.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/client/TransferableHelper.java): `handleURLList` → `if (Token.isTokenFile(url.getPath())) { Token token = PersistenceUtil.loadToken(url); … } else … Type.INVALID → dragdrop.unsupportedType`).
- **`.cmpgn` / campaign file, or the add-on game-data store: overkill / not a token.** A `.cmpgn` is a whole campaign; a token import should not demand the DM replace their campaign. The JSON game-data store is add-on storage ([docs: MapTool Data](https://docs.rptools.info/docs/data/) — "In MapTool 1.11 a new data store was created that is independent of tokens … only being used for Add-On data storage"), not a dropped token.

So the flow is: **mythosCircle serializes one NPC as a `.rptok`; the DM downloads it and drags it onto a map.** This matches the 5-2/5-3 lesson: implement against the real surface a DM actually uses.

### Why not the "RPGToken JSON" epic shape

The epic name *"RPGToken JSON"* is **not a shipped file format** in current MapTool. The JSON serialization that exists is (a) the protobuf JSON *data store* `DataStoreDto`/`data/game-data.json` written into `.cmpgn` files ([PersistenceUtil.saveGameData](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java#L1000-L1021), `GAME_DATA_FILE = "data/game-data.json"`), and (b) the network `TokenDto` protobuf ([Token.toDto/fromDto](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/model/Token.java#L3076-L3190)) used for client/server sync, not for on-disk token files. Neither is a downloadable single-token file a DM imports. **Conclusion: the canonical shape a mythosCircle export must produce is the `.rptok` ZIP; the JSON token concept is at most a future/aspirational format that we should not target.** (Marked, not padded.)

---

## 2. Verified format shape: the `.rptok` (PackedFile ZIP)

### 2a. Version of the client this targets

- **Current stable release: MapTool 1.18.6**, tag [`1.18.6`](https://github.com/RPTools/maptool/releases/tag/1.18.6), published 2025-10-30, non-prerelease (GitHub `releases/latest` → `tag_name: "1.18.6"`; installer assets `MapTool-1.18.6-*.exe/.dmg/.zip`). Nightly builds target `develop` (e.g. `nightly-20260913`). Default branch is **`develop`** ([repo metadata](https://api.github.com/repos/RPTools/maptool): `default_branch: "develop"`). Source links below are therefore to `develop`, which is the current development line (ahead of the 1.18 stable); all token-format mechanics cited are unchanged in that line.
- Version saved into every token file: `pakFile.setProperty(PROP_VERSION, MapTool.getVersion())` ([PersistenceUtil.saveToken](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java#L655-L687)). `versionCheck` warns/aborts only when the file's version is NEWER than the client ([PersistenceUtil.versionCheck](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java#L1064-L1075)). An exporter should set `version` to a value ≤ 1.18.6 (e.g. `"1.18.6"`).

### 2b. File layout (verified from source + official wiki)

A `.rptok` is **an ordinary ZIP file** ([wiki: Token](https://wiki.rptools.info/index.php/Token): "The actual format of the file … is an ordinary ZIP file!"). Internally (wiki + code agree):

| Entry | Content | Verified in |
| --- | --- | --- |
| `content.xml` | The `net.rptools.maptool.model.Token`, XStream-serialized to UTF-8 XML. Written by `pakFile.setContent(token)`; read by `pakFile.getContent(progVersion)`. | [PackedFile](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/lib/io/PackedFile.java), `CONTENT_FILE = "content.xml"`; [PersistenceUtil.saveToken/loadToken](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java#L655-L730) |
| `properties.xml` | Map of properties; keys `version` (string, MapTool version), `herolab` (bool). | `PROPERTY_FILE = "properties.xml"`; `setProperty(PROP_VERSION,…)` / `setProperty(HERO_LAB,…)` in `saveToken` |
| `thumbnail` | Small PNG (constrained to 50px) for library preview. | `Token.FILE_THUMBNAIL = "thumbnail"`; `saveToken` writes it |
| `thumbnail_large` | Larger preview PNG. | `Token.FILE_THUMBNAIL_LARGE = "thumbnail_large"`; `saveToken` |
| `assets/<md5>` | Per-asset XML descriptor (root `<net.rptools.maptool.model.Asset>`; fields `id`, `name`, `extension`, `type`) — REQUIRED for the asset to load. | `PackedFile.getAsset(path)` reads this XML ([PackedFile.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/lib/io/PackedFile.java) L590-…: parses `id`, `name`, `extension`, `type` from `assets/<md5>`); Asset fields `@XStreamAlias("id") md5Key`, `name`, `extension`, `type` ([Asset.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/model/Asset.java)) |
| `assets/<md5>.<ext>` | The actual binary image (e.g. `assets/<md5>.png`). | `PackedFile.getAsset`: `byte[] image = getFileAsInputStream(path + "." + extension)`; `PersistenceUtil.saveAssets` writes `ASSET_DIR + assetId + "." + extension` |

`Token.FILE_EXTENSION = "rptok"` ([Token.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/model/Token.java#L143)); size-detection filter `FILENAME_FILTER_TOKEN` = `name.endsWith("rptok")` ([AppConstants.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/client/AppConstants.java#L55-L57)).

>`<md5>` is a **32-char lowercase MD5 hex** of the image bytes (MD5Key → hex; [MD5Key.java vendored](https://github.com/RPTools/maptool/blob/main/src/main/java/net/rptools/lib/MD5Key.java): `encodeToHex` of the 16-byte md5). The wiki's older text saying "16-character checksum" is stale; asset filenames are the full 32-hex MD5.

**Required vs optional for a loadable file** (from `PersistenceUtil.loadToken` + `PackedFile`): `content.xml` (Token) and at least the `assets/<md5>`+`assets/<md5>.<ext>` pair for every image the token references are required for those images to appear. `properties.xml` with a `version` ≤ client is effectively required (missing `version` → `versionCheck` treats as `"0"`, still loads, but omit at your own risk); `thumbnail`/`thumbnail_large` are optional — `loadToken` does not read them (only `getTokenThumbnail` does, [PersistenceUtil.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java#L604-L620)). A missing/undecodable asset is **skipped with a log error, not a hard failure**; a missing/undecodable `content.xml` shows an error dialog and returns `null` (token not placed) — i.e. the client *does* reject malformed token files (`ConversionException` → `PersistenceUtil.error.tokenVersion`; `IOException` → `PersistenceUtil.error.tokenRead`, [PersistenceUtil.loadToken](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java#L704-L727)).

### 2c. Field/type inventory of the Token that must live in `content.xml` (names are the Java fields — XStream default aliases)

MapTool configures a bare XStream with no aliases for Token ([PersistenceUtil.getConfiguredXStream](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java#L299-L309)), so the **XML element name for each value is exactly the Java field name** and the root element is the FQCN `<net.rptools.maptool.model.Token>`. Fields we set for a mythosCircle NPC (all from [Token.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/model/Token.java)):

| content.xml element | Java field | Type / value we write | MapTool meaning |
| --- | --- | --- | --- |
| `<name>` | `private String name = "";` (L~370) | entity name, e.g. `Miriam Valeh, Guard Captain` | Shown on token/map |
| `<tokenType>` | `private String tokenType = Type.NPC.toString();` (L279) | `NPC` | MapTool PC/NPC kind (the D&D creature type goes in prose — MapTool has no structured creature-type field) |
| `<notes>` | `private String notes;` (L350) | the full 5e stat block text + a `Type/Race` + `Alignment` line (HTML allowed) | visible to whoever can view token; the de-facto free-text stat block carrier |
| `<gmNotes>` | `private String gmNotes;` (L354) | mythosCircle **lore notes** | GM-only notes ([Introduction to Tokens](https://wiki.rptools.info/index.php/Introduction_to_Tokens): "GM Notes … only the GM(s) should see") |
| `<gmName>` | `private String gmName;` (L357) | optional hidden GM name | GM-only alternate name |
| `<propertyType>` | `private String propertyType = …getDefaultTokenPropertyType();` (L283) | the campaign property-set name, e.g. `Basic` (**UNVERIFIED** default name) | selects which stat *property set* the token exposes; campaign-dependent, so do NOT rely on it as the stat block home |
| `<currentImageAsset>` | `private String currentImageAsset;` (L~345) | key into `imageAssetMap` (e.g. empty for the single default image) | which image renders on the map |
| `<portraitImage>` | `private MD5Key portraitImage;` (L328) | 32-hex MD5 of the embedded portrait PNG | portrait shown when hovering the token |
| `<charsheetImage>` | `private MD5Key charsheetImage;` (L327) | optional handout PNG md5 | "Show Handout" right-click item |
| `<imageAssetMap>` | `Map<String,MD5Key> imageAssetMap` | map: default image under key `null` (and optionally `"Portrait"`/`"Handout"`) | all image assets the token can display |

**No structured 5e stat block exists.** Confirmed by the wiki ([Introduction to Tokens](https://wiki.rptools.info/index.php/Introduction_to_Tokens)), which defines the only built-in token "stat" surface as **Properties** — a campaign-specific set (default: `Strength, Dexterity, Constitution, Intelligence, Wisdom, Charisma, HP, AC, Defense, Movement, Elevation, Description`) with no fixed `Type`/`Alignment`/`Speed`/`CR` slots. Therefore:
- **Attributes, AC, HP** COULD go into `propertyType`'s set, but only if the DM's campaign happens to define those property names; this is fragile and campaign-dependent → do **not** make it the primary carrier.
- The reliable, version-independent carrier for a **5e stat block as text** is the token's **`notes`** field (HTML, visible) — put the whole stat block there (see worked example). Put GM-only **lore** in **`gmNotes`**.
- **Race/type + alignment**: MapTool has no fields for these (the token's `type` is the PC/NPC enum). Express them as the first lines of `notes` (e.g. `Medium Humanoid, Lawful Neutral — Human (Guard Captain)`), matching how a 5e stat block opens.

### 2d. Minimal viable exporter contract (what mythosCircle must build)

Produce a ZIP named `…​.rptok` containing at minimum: `content.xml` (Token XML), `assets/<md5>` (Asset XML descriptor incl. `id`/`name`/`extension`/`type`), `assets/<md5>.png` (portrait bytes), and `properties.xml` (a map with `version` = `1.18.6` and `herolab` = `false`). `thumbnail`/`thumbnail_large` optional (generate for library previews). All binary asset entries are the raw PNG (no base64) — the wiki's note about XML-embedded base64 applies only to pre-1.3b64 files and is explicitly called a bug; current format stores real image bytes ([PackedFile.saveEntry](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/lib/io/PackedFile.java) writes ZIP entries from `getFileAsInputStream`).

---

## 3. Full worked-example token (5e NPC)

Example entity: **"Miriam Valeh, Guard Captain"** — Medium humanoid, Lawful Neutral; STR 16 (+3), DEX 12 (+1), CON 14 (+2), INT 8 (−1), WIS 12 (+1), CHA 11 (+0); AC 16 (chain shirt); HP 45 (6d10+12); Speed 30 ft.; a couple of traits/actions; lore notes; one portrait PNG. In MapTool terms: `name = "Miriam Valeh, Guard Captain"`, `tokenType = "NPC"`, stat block → `notes`, lore → `gmNotes`, portrait PNG embedded and referenced as the default image + portrait.

Portrait bytes → `md5 = md5hex(portrait.png)` (32 lowercase hex). The `.rptok` zip contains:

```
content.xml
properties.xml
assets/<md5>
assets/<md5>.png
thumbnail
thumbnail_large
```

**`content.xml`** (root `<net.rptools.maptool.model.Token>`; element names = Java fields, verified against Token.java; XStream boilerplate such as the exact `<id>` wrapper and the full default-valued technical field set is marked UNVERIFIED — §5):

```xml
<net.rptools.maptool.model.Token>
  <id><id>9F4B7A1C2D3E4F5A6B7C8D9E0F1A2B3C</id></id> <!-- random 16-byte GUID, uppercase hex; UNVERIFIED wrapper -->
  <name>Miriam Valeh, Guard Captain</name>
  <gmName></gmName>
  <tokenType>NPC</tokenType>
  <layer>Token</layer><!-- default player layer; UNVERIFIED exact string -->
  <propertyType>Basic</propertyType><!-- campaign-default property set; UNVERIFIED default name -->
  <notes>&lt;b&gt;Medium Humanoid, Lawful Neutral&lt;/b&gt;&lt;br&gt;Human (Guard Captain)&lt;br&gt;&lt;br&gt;
  Armor Class 16 (chain shirt)&lt;br&gt;Hit Points 45 (6d10 + 12)&lt;br&gt;Speed 30 ft.&lt;br&gt;&lt;br&gt;
  STR 16  DEX 12  CON 14  INT 8  WIS 12  CHA 11&lt;br&gt;
  Saving Throws Str +5, Con +4&lt;br&gt;Skills Athletics +5, Perception +4&lt;br&gt;
  Senses Passive Perception 14&lt;br&gt;Languages Common&lt;br&gt;Challenge 1 (200 XP)&lt;br&gt;&lt;br&gt;
  Traits — <span>Vigilant.</span> …&lt;br&gt;Actions — <span>Spear.</span> Melee … Hit: 8 (1d8+3) piercing.&lt;/notes>
  <notesType>text/rsyntaxtextarea/none</notesType><!-- UNVERIFIED exact enum string -->
  <gmNotes>Carries the keys to the east barracks. Knows the captain's password. Fears the warden. Secretly reports to the spymaster.</gmNotes>
  <currentImageAsset></currentImageAsset><!-- the single default image = portrait -->
  <portraitImage><id>abcd…(32 hex)</id></portraitImage><!-- UNVERIFIED wrapper -->
  <imageAssetMap>
    <entry><string></string><md5-key>abcd…(32 hex)</md5-key></entry><!-- default (null) key → portrait PNG -->
  </imageAssetMap>
</net.rptools.maptool.model.Token>
```

*(Assuming portraits carry HTML; MapTool notes support basic HTML per the wiki. `&lt;span&gt;` is optional; plain text is safer for a first build.)*

**`properties.xml`** (a serialized `Map<String,Object>` with keys `version` and `herolab`):

```xml
<map>
  <entry><string>version</string><string>1.18.6</string></entry>
  <entry><string>herolab</string><boolean>false</boolean></entry>
</map>
```

**`assets/<md5>`** (Asset XML descriptor — parse contract in `PackedFile.getAsset`):

```xml
<net.rptools.maptool.model.Asset>
  <id><id>abcd…(32 hex)</id></id>  <!-- md5, alias "id" -->
  <name>Miriam Valeh, Guard Captain</name>
  <extension>png</extension>
  <type>IMAGE</type>
</net.rptools.maptool.model.Asset>
```

**`assets/<md5>.png`** = the raw PNG bytes. `thumbnail` / `thumbnail_large` = small PNG previews of the same portrait (constrained to 50px and to `ThumbnailSize` respectively, per `saveToken`).

> Exact XStream wrappers for `GUID`, `MD5Key`, and the `Map` serialization, plus the full default-valued technical field set emitted by a real MapTool save, are **UNVERIFIED** — the exporter must be pinned against a real saved `.rptok` at spec time (an open `zip` with `unzip -d` shows `content.xml` verbatim). Structurally the offered element names are the exact field names from Token.java, which is what matters for XStream to map them back.

---

## 4. Portrait reality

MapTool **generates and stores images as bytes in the token file** — there is no remote-URL image field.

- A token references its images by `MD5Key` only: `getAllImageAssets()` returns `imageAssetMap.values() + charsheetImage + portraitImage` ([Token.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/model/Token.java#L1284-L1287)); `saveToken` embeds every one of these under `assets/` via `saveAssets` ([PersistenceUtil.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java#L1060-L1080): `pakFile.putFile("assets/"+id+"."+ext, data)` + `putFile("assets/"+id, asset)`); `loadToken` reads them back from the same packed file.
- **A signed URL cannot be the portrait.** The wiki's own "Modifying the Images in a Token File" section confirms images live inside `assets/` and are referenced by checksum in `content.xml` ([wiki: Token](https://wiki.rptools.info/index.php/Token)). There is no URL-valued image property on Token.
- Portrait is a distinct field (`portraitImage`) shown when hovering; **Handout** (`charsheetImage`) is the right-click "Show Handout" image. For a single-portrait NPC, use the same embedded PNG for the default token image (`imageAssetMap[null]`) and `portraitImage` (and optionally `charsheetImage`).
- **Feasible for mythosCircle:** the portrait PNG is already generated on disk, so embedding bytes is trivial (the 5-2 "never embed binaries" rule was about *Forge/Owlbear payloads*; this target's only valid form *is* embedded bytes). Size: one PNG ≈ tens–hundreds of KB.
- The signed URL's remaining role is presentation only (as in 5-2/5-3): the download page can show the portrait; the `.rptok` carries the same bytes.

---

## 5. Open questions / checks still owed at spec time

1. **Exact serialized shape of a real `content.xml`.** The authoritative verification is to open a `.rptok` produced by MapTool 1.18.6 (`unzip -d out x.rptok`) and copy `content.xml` verbatim. Needed to pin: the exact XStream wrappers for `GUID` (token `id`), `MD5Key` values, the `<imageAssetMap>` map serialization (null-key entry), and whether MapTool emits every default-valued technical field (`x`,`y`,`z`,`width`,`height`,`snapToGrid`, states/halos/light lists, etc.) — our minimal exporter omits them and relies on XStream `ignoreUnknownElements` + Token.`readResolve` defaults ([Token.java readResolve](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/model/Token.java#L2630)), which the code supports but which must be proven against the real client. **UNVERIFIED.**
2. **Exact `assets/<md5>` Asset descriptor XML.** `PackedFile.getAsset` parses `id` (nested), `name`, `extension`, `type`; the precise element nesting should be copied from a real dump. **UNVERIFIED.** If hand authoring proves brittle, the fallback is to let MapTool generate the descriptor by (dev-only) dragging the portrait PNG onto a map and using right-click → Save As on that token, then reading the produced `assets/` files.
3. **Default `propertyType` / `layer` / `notesType` strings.** Names sourced from enums/I18N keys, exact strings not verified. Harmless if slightly off (defaults apply), but pin from a real file. **UNVERIFIED.**
4. **Confirm drag-drop places the token on the Token layer by default** (so it is a movable NPC, not an object/stamp). The renderer's `drop` → `addTokens` path ([ZoneRenderer.java](https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/client/ui/zone/renderer/ZoneRenderer.java#L2438-L2447)) exists; live-check: drag a `.rptok` and confirm it lands on the Token layer with the name shown. (The `layer` field we set controls this.)
5. **No original SVG/portrait-only format confusion:** only raster (PNG/JPEG) is valid for token images (MapTool token images are bytes; PNG recommended for transparency per the wiki). If mythosCircle's portrait is already PNG, no conversion needed.

## Source index (all external, public)

- MapTool repo default branch + license: https://api.github.com/repos/RPTools/maptool (`default_branch: "develop"`; SPDX `AGPL-3.0`).
- Stable release **1.18.6**: https://github.com/RPTools/maptool/releases/tag/1.18.6 (published 2025-10-30).
- `PersistenceUtil.java` (save/load token, version, assets, game-data JSON): https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/util/PersistenceUtil.java
- `PackedFile.java` (ZIP + `content.xml`/`properties.xml`/`assets` read/write, `getAsset`): https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/lib/io/PackedFile.java
- `Token.java` (fields `name/gmName/notes/gmNotes/portraitImage/charsheetImage/tokenType/propertyType/imageAssetMap`, `FILE_EXTENSION="rptok"`, `getAllImageAssets`, `readResolve`, `toDto/fromDto`): https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/model/Token.java
- `Asset.java` (Asset XML fields incl. `@XStreamAlias("id") md5Key`): https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/model/Asset.java ; vendored `MD5Key.java`: https://github.com/RPTools/maptool/blob/main/src/main/java/net/rptools/lib/MD5Key.java
- `AppConstants.java` (rptok filter + `CAMPAIGN_FILE_EXTENSION=".cmpgn"`): https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/client/AppConstants.java
- `TransferableHelper.java` (drag-drop: `.rptok` → `PersistenceUtil.loadToken`, unsupported-type rejection): https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/client/TransferableHelper.java
- `ZoneRenderer.java` (drop → `addTokens`): https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/client/ui/zone/renderer/ZoneRenderer.java
- `TokenPopupMenu.java` (right-click menu incl. `SaveAction`, i.e. the "Save As…/Export" path) + `AbstractTokenPopupMenu.java`: https://github.com/RPTools/maptool/blob/develop/src/main/java/net/rptools/maptool/client/ui/TokenPopupMenu.java
- Official wiki — **Token file format** (ZIP, content.xml/properties.xml/assets/thumbnail, images by checksum): https://wiki.rptools.info/index.php/Token
- Official wiki — **Introduction to Tokens** (create token by dragging file/Rsrc Library; Notes/GM Notes; Portrait/Handout; Properties the only "stats" carrier; Edit Token dialog PC/NPC): https://wiki.rptools.info/index.php/Introduction_to_Tokens
- Official docs — **MapTool Data** (JSON game-data store, 1.11+, add-on scoped): https://docs.rptools.info/docs/data/