# 74 — Machine MAITS : dix moteurs d'après Plaits

Demande de DavidCaruso (22/10/2026) : proposer au projet la machine PLAITS de sa Plaits Edition de Model-TG, sous le
nom **MAITS**, d'abord seule (« Standalone first ») : des tweaks complets et prouvés, **hors de `os.json`** ; l'entrée
dans l'OS sera une PR à part (§6). Tweaks `27-maits.json` (seul) et `36-maits-tg.json` (avec Model-TG), générateur
`tools/gen_maits.py`, sources `tools/machines/maits/` (`engine.s` les dix moteurs, `maits.c` la passerelle,
`mi_tables.inc` généré), preuve `tools/emu/test_maits.py`. Adresses : VA de l'OS 1.13.

## En bref

- **Une machine ajoutée, MAITS**, après Chord (après le Sampler avec Model-TG), avec la mécanique de MACRO et d'ACID
  (`gen_macro.added_machine`, [43](43-machine-macro.md), [54](54-machine-acid.md)) : mêmes détours, mêmes tables
  déplacées, même place dans la charge utile. Icône de TONE.
- **Les quatre réglages de Plaits sur les potards du Cycles** : COLOR = HARMONICS, SHAPE = TIMBRE, SWEEP = MORPH,
  CONTOUR = le moteur (0 à 9, son numéro à l'écran). PITCH, FINE, la note du trig, DECAY, GATE et PUNCH comme sur TONE
  (sa chaîne d'ampli joue le rôle du *low-pass gate* de Plaits). La vélocité passe par l'OS, comme partout.
- **Dix moteurs** : WSHP (waveshaping), FM (2 opérateurs), SWRM (nuée de grains), GRAN (deux grainlets, formants),
  PD (distorsion de phase), CHIP (carré synchronisé, accords et arpège), NOIS (bruit filtré), MODL (résonateur
  modal), PART (poussière dans des filtres résonants), STRG (corde de Karplus-Strong).
- **Le son est celui de la Plaits Edition, instruction pour instruction** : `engine.s` est le `plaits.inc` de la
  Plaits Edition ; seules ses entrées et sorties ont changé (§3). Recoupé bloc par bloc contre la Plaits Edition
  elle-même : 1200 blocs, 0 écart (§7).
- **Hors de l'OS pour l'instant** : l'OS 2.0 a déjà sept machines ajoutées (Sampler, 5 moteurs du Syntakt, MACRO,
  ACID) ; une huitième demande la place d'une huitième machine dans `gen_syntakt_engines.py` et le fichier du Syntakt
  pour construire et prouver la version combinée (§6).

## 1. D'où vient le son

Les moteurs sont des **réécritures en virgule fixe** des algorithmes de Plaits (Mutable Instruments, Émilie Gillet,
licence MIT) pour le ColdFire sans FPU, écrites pour la machine PLAITS de la Plaits Edition de Model-TG (dépôt de
DavidCaruso, MIT). **Aucune ligne du C++ de Plaits n'est reprise** ; quelques tables (`mi_tables.inc` : sinus,
exponentielle, fenêtres, rapports du modal) sont **calculées** par `gen_maits.py` à partir des formules de Plaits
(portage de `gen_mi_tables.py` de la Plaits Edition, mêmes valeurs). Licence de Plaits recopiée dans
`tweaks/model-cycles_OS1.13/LICENSE-Plaits` `[FAIT]`.

| # | Écran | Moteur | HARMONICS (COLOR) | TIMBRE (SHAPE) | MORPH (SWEEP) | Calcul |
|---|---|---|---|---|---|---|
| 0 | WSHP | waveshaping | forme du shaper | repli | pente | 96 kHz |
| 1 | FM | FM à 2 opérateurs | rapport | index | réinjection | 96 kHz |
| 2 | SWRM | nuée de grains | écart | densité | longueur | 48 kHz |
| 3 | GRAN | deux grainlets | rapport | formant | forme | 96 kHz |
| 4 | PD | distorsion de phase | rapport | quantité | asymétrie | 96 kHz |
| 5 | CHIP | carré synchronisé | accord | arpège | sync | 48 kHz |
| 6 | NOIS | bruit filtré | type | horloge | résonance | 48 kHz |
| 7 | MODL | résonateur modal | matériau | brillance | amortissement | 48 kHz |
| 8 | PART | poussière | écart | densité | résonance | 48 kHz |
| 9 | STRG | corde (KS) | chevalet | brillance | amortissement | 48 kHz |

## 2. Ce que l'OS donne à une machine `[FAIT en émulation]`

Le contrat de la boucle des voix de [14](14-machine-sd-vintage.md) et [43](43-machine-macro.md) : `update(pmod, v, p)`
puis `render(out, v)`, une fois par bloc de 32 trames à 48 kHz. `pmod` = la note du trig (demi-tons << 16), `v` = la
voix (0x31c o, `0x42308828 + 0x31c × piste`), `p` = les paramètres de la piste en mots 8.8 (`+0x14` PITCH, `+0x16`
COLOR, `+0x18` SHAPE, `+0x1a` SWEEP, `+0x1c` CONTOUR, `+0x1e` PUNCH, `+0x20` GATE, `+0x22` FINE, `+0x24` DECAY) ;
`v+0x34` / `v+0x38` : un trig ce bloc / le bloc d'avant. Sortie : 32 × int32, pleine échelle des moteurs à ½ Q31,
comme MACRO.

## 3. Le portage (`engine.s`)

`plaits.inc` rend dans la fenêtre du Sampler de Model-TG ; ici, il rend dans le BSS de la charge utile :

- `mi_fill(piste, note, 0)` : la **note** (Q8 demi-tons, 60 << 8 = do du milieu) remplace le pas de lecture du
  Sampler ; `mi_n2inc` la convertit en incrément de phase à 96 kHz ;
- `mi_trig` (une note ce bloc) remplace `dr_trig`, `mi_obuf` (64 × 4 o) remplace `sampler_buf+32`, `mi_dly` (6 ×
  8192 o, lignes à retard de STRG) remplace `DLY_BASE` ; le test `pk_init` de STRG disparaît (seul `mi_dz` reste) ;
- **pas de `.data`** (interdit dans une machine ajoutée, la charge utile est remise à zéro) : `mi_eng` garde
  moteur + 1 (0 = aucun) au lieu de -1 ;
- **un bruit par piste** : la Plaits Edition a un seul LCG (`mi_seed`) pour toutes les pistes, si bien qu'une piste
  ne sonne pas pareil selon ce que jouent les autres (SWRM, GRAN, NOIS, PART, STRG). Ici `mi_fill` charge à
  l'entrée le LCG de la piste dans `mi_seed` et le range à la sortie (`mi_out`) dans `mi_seeds`, rangé « xor la clé
  de la piste » (piste × `0x9e3779b9`, `mi_skey`) : depuis les zéros du BSS, chaque piste part de sa propre suite
  (la piste 0 de 0, comme la Plaits Edition) et deux pistes ne jouent jamais le même bruit. Les moteurs eux-mêmes
  ne changent pas ;
- pas d'a6/fp, pas d'EMAC (le MACSR de la boucle des voix ne compte pas) ; garde d2–d7/a2–a5.

Tailles : code 6 690 o, tables 5 574 o, variables 50 340 o (dont 49 152 de lignes à retard) `[FAIT]`.

## 4. La passerelle (`maits.c`)

- **`maits_update`** : à la première note sur la machine (repère `'MAI1'` à `v+0x2c`, effacé par la remise à zéro de
  la voix `0x400a7ab8`), l'état des moteurs repart de zéro (`mi_eng[t] = 0`, historique du filtre effacé). La chaîne
  d'ampli est réglée comme celle de TONE (`0x400aa7b8`) : DECAY par sa table `0x4011d84c` dans `v+0x250`, et à un
  trig (`v+0x38`) les constantes d'enveloppe de TONE, `v+0x48 = 0`, `mi_trig[t] = 1`. La note = celle du trig +
  (PITCH − 64) + FINE (±2 demi-tons), bornée à 0..143. Les trois cadrans en Q15 (0..0x7f00) ; CONTOUR >> 8, borné
  à 9, donne le moteur, au milieu de son dixième du cadran (`(n × 32768 + 16384) / 10`, ce que lit `mi_eidx`).
- **`maits_render`** : une voix muette (pas de trig, enveloppe d'ampli sous `1 << 14`, −102 dB) n'est pas calculée
  (zéros). Sinon `mi_fill` donne 64 échantillons à 96 kHz ; le **demi-bande à 23 coefficients de MACRO**
  ([43 §3.2](43-machine-macro.md), `{10327, −3131, 1579, −820, 434, −197}`, Q15, gain 1 en continu) en garde un sur
  deux : 32 à 48 kHz. Puis `AMP_ENV` `0x400a9252`, `VCA` `0x400a9430`, `PUNCH` `0x400a967a`, ceux de TONE.

Niveau : avec DECAY 127, RMS de la sortie de −27 à −41 dBFS selon le moteur (WSHP −30, FM −28, SWRM −36, GRAN −32,
PD −27, CHIP −27, NOIS −33, MODL −41, STRG −40), dans la plage de MACRO (−27 à −37 dBFS mesurés de la même façon,
SHAPE 0..30) `[FAIT en émulation]`. Aucun gain ajouté : la pleine échelle des moteurs est celle de MACRO.

PART, TIMBRE (densité) au milieu : la densité de Plaits va de la note 60 à 132 au carré, soit quelques grains par
seconde à TIMBRE ≤ 64 : le premier grain arrive 0,1 s après le trig à TIMBRE 64 (bloc 150), 0,47 s à TIMBRE 32
(bloc 702). C'est le comportement de Plaits et de la Plaits Edition, pas un défaut ; à TIMBRE 127, −35 dBFS
`[FAIT en émulation]`.

## 5. Place

- **Seul** (`27-maits.json`) : charge utile à `0x43000000` (`PAY_ALONE`), 272 556 o une fois en place (code et
  tables, détours, données copiées, puis ses variables à `+0x36000`), **rangée en 18 628 o** dans l'image
  (compressée avec l'OS) ; image décompressée jusqu'à `0x401ae9c4`, sous `0x40200000` `[FAIT en émulation]`.
- **Avec Model-TG** (`36-maits-tg.json`) : à `0x46700000` (`PAY_TG`), rangée en 19 040 o.
- Le code finit avant `gs.STUBS` (`+0x33000`).

## 6. Pourquoi pas encore dans l'OS

`maits` et `maits-tg` déclarent `conflicts` avec MACRO, ACID et les moteurs du Syntakt (même place de la charge
utile, mêmes détours), comme ACID seul avant ses versions combinées ([54 §11](54-machine-acid.md)). Les mettre dans
l'OS demande :

1. une **huitième machine ajoutée** dans `gen_syntakt_engines.py` (`MAX_EXTRA = 7`, la rangée de repères
   `MARKS_ROW`, `ROWS7` / `check_room`) et dans `gen_acid_combos.py` (MAITS après ACID, machine 15) ;
2. le fichier `Syntakt_OS1.42.syx` pour construire et prouver la version combinée (non disponible dans cette
   session : elektron.se n'est pas joignable) ;
3. la place : le code et les tables de MAITS (13 306 o) se compriment en ~11,1 Ko (aPLib, estimé), sous les
   16 948 o qui restent à l'OS 2.0 sous `0x40200000` `[HYP]` ;
4. la charge : à mesurer avec les autres machines ajoutées sous le régulateur des moteurs (`GOV_SKIP`).

Ce sera une PR à part, avec la feature dans `os.json` (`experimental`).

## 7. Preuve en émulation `[FAIT en émulation]`

`tools/emu/test_maits.py` (`--quick`, `--with`), sur le moteur de l'OS (`mcengine.py`) :

1. démarrage : décompresseur, crochet du BSS, charge utile reconstituée, SRAM intacte ;
2. interface : rangées de machines, descripteurs (Harmonic, Timbre, Morph, Engine 0..9), écran MACHINES (7 noms,
   repères), réglage et molette, enregistrements, potards de l'écran principal, machine hors limites, petite icône ;
3. son : `mi_fill` reçoit les cadrans, la note et le trig du §4, et le bloc à 48 kHz est **exactement** le demi-bande
   de sa sortie, à chaque bloc ; la chaîne d'ampli lit la voix comme TONE jusqu'au silence ; les dix moteurs sonnent
   et diffèrent ; les moteurs accordés (WSHP, FM, PD, CHIP, NOIS, STRG) à leur note à 10 cents près ; changement de
   moteur depuis le repos ; bornes ;
4. le stock inchangé, les machines d'origine identiques au stock ; p-locks ; six pistes ; pistes mêlées ;
5. charge : instructions par bloc par moteur, voix muette, TONE ; la pile ;
6. avec Model-TG (`maits-tg`), puis `--with` les autres tweaks de l'OS qui n'ont pas de conflit.

**Recoupement avec la Plaits Edition** (script hors dépôt, 22/10/2026) : la charge utile de `27-maits.json` dans un
Unicorn nu contre la machine PLAITS de la Plaits Edition dans son propre émulateur (`tests/va_emu.py`), incrément
de phase forcé au même nombre des deux côtés, dix moteurs × 3 réglages aléatoires × 40 blocs avec deux trigs :
**1200 blocs comparés, 0 écart**.

### 7.1 Résultats (22/10/2026)

`test_maits.py --quick` : démarrage et interface (27 vérifications) OK ; son : `mi_fill` et le demi-bande exacts à
chaque bloc (18 cas, crête 0,07 de la pleine échelle), chaîne d'ampli de TONE jusqu'au silence (blocs 120), les dix
moteurs sonnent et diffèrent. Passe complète et `--with` en cours (section suivante).

## 8. À essayer sur la machine `[À FAIRE]`

Avec `27-maits.json` seul (`build.py -t maits`) puis avec Model-TG (`-t model-tg-st,maits-tg`) :

- MACHINES : MAITS en 7ᵉ (8ᵉ avec Model-TG), icône de TONE ; les potards Harmonic, Timbre, Morph, Engine ;
- CONTOUR de 0 à 9 : les dix moteurs, chacun à sa note sur le clavier (WSHP, FM, PD, CHIP, NOIS, STRG) ;
- DECAY long et court, GATE, PUNCH ; p-locks de COLOR/SHAPE/SWEEP et du moteur ;
- six pistes MAITS à la fois : la charge (l'écran de la charge de Model-TG) ; PART et SWRM à TIMBRE haut ;
- retour à une machine d'origine avant de flasher autre chose.
