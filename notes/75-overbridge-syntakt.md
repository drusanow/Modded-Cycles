# 75 — Overbridge : le mode USB du Syntakt, et ce qu'il faudrait pour le Cycles (expérience)

Demande de DavidCaruso (22/10/2026) : rétro-ingénierie d'Overbridge dans l'OS du Syntakt, pour essayer de le
porter sur le Cycles, et s'en servir pour un « Sidecar » (un petit ordinateur sur l'USB du Cycles qui joue des voix
et renvoie leur son, à la manière du Sidecar Expander de trigcondition pour l'Analog Rytm MKII). Fichier étudié :
`Syntakt_OS1.42.syx` officiel (SHA-256 `ded3c59f…7f37c1`, `tools/emu/syntakt.py`), section 3 (MAIN OS, 3 442 576 o,
chargée à `0x40000400` comme celle du Cycles). Adresses : VA du Syntakt 1.42 sauf mention. Rien de l'OS n'est
recopié ici : seulement des structures, des tailles et des adresses.

## En bref

- **Overbridge 2, côté audio, est simple** : une configuration USB à interfaces « vendor », deux points d'accès
  **interrupt** à haute vitesse (EP 0x83 vers l'hôte, EP 0x03 depuis l'hôte, intervalle 1 micro-trame), des blocs de
  7 trames d'entiers 32 bits. Pas de compression, pas de chiffrement. Le démarrage se fait par `SET_INTERFACE` (alt 3)
  sur les interfaces 1 et 2 ; l'hôte libre Overwitch (dagargo, Linux) ne fait en plus que lire le nom de l'appareil
  par deux requêtes « vendor » sur EP0.
- **Le pilote USB du Cycles est de la même famille** (même contrôleur ChipIdea, même réserve de dTD en `0x80008000`,
  même table des modes de 0x28 o, même amorçage de 11 cases) : un mode Overbridge sur le Cycles serait un nouveau
  jeu de descripteurs, un remplisseur de blocs à la place de `0x40002912` ([35](35-glitches-usb-multipiste.md)) et la
  gestion de `SET_INTERFACE`, pas un nouveau pilote.
- **Le logiciel Overbridge d'Elektron ne reconnaîtra pas un Cycles** (aucun plugin) ; l'hôte de l'expérience est
  **Overwitch** (GPL-3, Linux, Raspberry Pi compris), qui accepte des appareils définis par l'utilisateur
  (`~/.config/overwitch/devices.d/*.json` : PID, nom, pistes). Un PID propre au Cycles suffit : pas besoin de se
  faire passer pour un Syntakt.
- **Un Pico 2 (RP2350) ne peut pas être l'hôte** : son USB est Full Speed (12 Mbit/s) ; Overbridge demande la haute
  vitesse (un bloc de 592 o toutes les 125 µs ≈ 4,7 Mo/s, le Full Speed plafonne à 64 o par ms en interrupt). Un
  Raspberry Pi 4 ou 5 (hôte USB 2.0 HS, Linux) convient.

## 1. La table des modes USB `[FAIT]`

`0x4029a830`, 6 entrées de 0x28 o (le Cycles : `0x4013e544`, 4 entrées, [09](09-analyse-firmware-1.13.md)) :
+0 activation, +4 arrêt, +8 descripteur d'appareil, +12 configuration, +16 longueur de la configuration, +28 table
des chaînes, +32 nombre de chaînes (9), +36 haute vitesse (1).

| Entrée | Mode | PID | Configuration | Fonctions |
|---|---|---|---|---|
| 0, 1 | USB MIDI (FS, HS) | `0x001f` | `0x40339203` / `0x4033919e`, 101 o | `0x400047ae` / `0x400047a4`, `0x4000465a` |
| 2, 3 | CDC (mise à jour) | `0xffff` | `0x40339141` / `0x403390f6`, 75 o | `0x4000559c` / `0x40005592`, `0x400053e8` |
| 4 | USB AUDIO/MIDI (UAC2, isochrone) | `0x104a` | `0x40339393`, 328 o | `0x40004394`, `0x40004342` |
| 5 | **OVERBRIDGE** | `0x0b4a` | `0x4033927a`, 263 o | `0x400038fe`, `0x400038e4` |

Menu : `OVERBRIDGE`, `USB MIDI`, `USB AUDIO/MIDI` (chaînes en `0x402589c8`).

## 2. La configuration Overbridge (263 o) `[FAIT]`

| Interface | Alt | Classe | Point d'accès |
|---|---|---|---|
| (IAD 0..3) | | vendor | |
| 0 | 0 | vendor | aucun (contrôle) |
| 1 | 0 / 1 / 2 / 3 | vendor | EP **0x83** interrupt, intervalle 1, 0 / 32 / 88 / **592** o |
| 2 | 0 / 1 / 2 / 3 | vendor | EP **0x03** interrupt, intervalle 1, 0 / 32 / 88 / **256** o |
| 3 | 0 | vendor | EP 0x02 bulk 512 o, EP 0x82 interrupt 64 o (le protocole du plugin) |
| (IAD 4..5) 4 | 0 | audio / control | aucun |
| 5 | 0 | audio / MIDI streaming | EP 0x01 et 0x81 bulk 512 o (MIDI USB ordinaire) |

Activation (`0x400038fe`) : dQH des points d'accès 7 (EP3 IN) et 3 (EP3 OUT) remis à zéro (capacités `0x04000000`,
dTD suivant `0xdead0001`), tampons SRAM `0x80008000..0x8000c100` remis à zéro, puis `ENDPTCTRL3` (`0xfc0b01cc`)
`= 0x00cc00cc` : EP3 dans les deux sens, type **interrupt**, actif, bascule remise à zéro.

## 3. Les blocs `[FAIT en émulation]`

Vérifié en faisant tourner le remplisseur du Syntakt (`0x40003458`) dans Unicorn avec des échantillons repérables.

**Vers l'hôte (EP 0x83), 592 o par bloc, un bloc par transfert** :

| Octets | Contenu |
|---|---|
| 0 | `0x07` (trames par bloc) |
| 1 | `0x00` |
| 2..3 | compteur de trames, 16 bits gros-boutiste, +7 par bloc |
| 4..31 | 7 mots longs « annexes », un par trame (second argument du remplisseur) |
| 32..591 | 7 trames × **20 canaux** × entier 32 bits gros-boutiste, entrelacées par trame |

**Depuis l'hôte (EP 0x03), 256 o** : même en-tête (Overwitch y met `0x07ff` puis son compteur de trames), 28 o
annexes, puis 7 trames × **8 canaux** × 32 bits.

Overwitch confirme la forme (« o2h block » = 32 + 7 × taille de trame) et l'envoi de 24 blocs par transfert côté
hôte ; le Syntakt, lui, met un bloc par dTD.

- **Ring vers l'hôte** : 16 blocs de 592 o en `0x80008c00`, dTD (64 o) pris dans la réserve de 32 en `0x80008000`
  (`0x40002fc0`), jeton `0x02500080` (592 o, actif), mis en file par `0x40005d72(7, dTD)`.
- **Démarrage** (`SET_INTERFACE` interface 1, alt ≠ 0 → `0x40003376`) : 11 blocs de silence amorcés, comme le stéréo
  du Cycles ; alt 0 → arrêt (`0x400033bc`). La taille ne dépend pas de l'alt choisi : l'hôte doit prendre l'alt 3.
- **Depuis l'hôte** (interface 2, alt ≠ 0 → `0x400033f0`) : 16 dTD de réception de 256 o en `0x8000b100`
  (`0x400031ec`), lus par `0x40003686` (8 canaux, 32 o par trame).
- **Rappels de fin de transfert** (`0x40002ffc`, `0x400030f8`) : seulement l'horodatage (numéro de trame USB,
  `0x454e4c0c`) pour aligner le flux au démarrage ; l'horloge de référence reste celle du Syntakt (l'hôte
  rééchantillonne, Overwitch le fait avec libsamplerate).
- **Appel par bloc audio** : `0x40003458(0x80004760, 0x43bdd768, 32)` en `0x400a52ae` : 32 trames de 20 canaux
  entrelacés en SRAM (`0x80004760`, 2 560 o), et un bloc annexe de 128 o rempli juste avant (niveaux, octets d'état ;
  probablement les vumètres et l'état pour le plugin `[HYP]`). Sans son (`0x43bdd4c4`), le même appel avec des zéros.

Requêtes sur EP0 (d'après le code d'Overwitch, lu pour les faits seulement, GPL) : `0xC0` (vendor, IN, appareil),
`bRequest` 1 et 2, `wValue` = `wIndex` = 0 : lecture du nom de l'appareil. Puis `SET_CONFIGURATION 1`,
`SET_INTERFACE` (1, 3) et (2, 3), `CLEAR_FEATURE(HALT)` sur 0x83 et 0x03. Les interfaces 4 et 5 restent au pilote
MIDI du système. `[À FAIRE]` : trouver le traitement des requêtes 1 et 2 dans le Syntakt.

## 4. Ce qu'il faudrait sur le Cycles (projet d'expérience)

1. **Descripteurs** : une configuration de la même forme, à la place de celle de l'audio USB (328 o en `0x4019b2b6`,
   la nôtre en ferait 263) ; un PID propre ; le MIDI USB du Cycles gardé (interfaces 4 et 5, ses EP 0x01 et 0x81).
2. **Activation** : `ENDPTCTRL3` en interrupt dans les deux sens, dQH des points d'accès 7 et 3 ; pas d'EP de retour
   de débit (le 0x82 isochrone du mode UAC2).
3. **`SET_INTERFACE`** : interface 1 → flux vers l'hôte, interface 2 → flux depuis l'hôte (l'inverse du mode UAC2 du
   Cycles) ; à trouver dans le pilote du Cycles (l'équivalent de `0x40006800`).
4. **Requêtes vendor 1 et 2** : répondre un nom (« Model:Cycles »).
5. **Remplisseur** à la place de `0x40002912(0x40fe4b90, 32)` ([35](35-glitches-usb-multipiste.md)) : par exemple
   8 canaux (mix G/D + 6 pistes, `0x80001858`) → blocs de 32 + 7 × 32 = 256 o ; envoi à heure fixe comme
   `usb_steady` ([35 §4](35-glitches-usb-multipiste.md)).
6. **Lecture depuis l'hôte** : 8 canaux (par exemple un retour stéréo dans le mix et 6 retours par piste pour le
   Sidecar), à la place de la lecture du flux UAC2 entrant.
7. **Place en SRAM** (les tampons DMA doivent être hors cache, comme ceux d'Elektron) : environ 16 × 256 o dans chaque
   sens, plus les dTD ; la carte de la SRAM du Cycles est à établir `[À FAIRE]`. En mode Overbridge, les rings du mode
   UAC2 (`0x80009800`…) sont libres et peuvent servir.
8. **Preuve** : le pilote USB du Cycles est déjà simulé ([35](35-glitches-usb-multipiste.md)) ; même méthode, avec un
   hôte simulé qui suit ce que fait Overwitch. L'essai réel demande un Cycles et un Linux (ou un Pi) avec Overwitch et un
   fichier de définition de l'appareil.

## 5. Le Sidecar

- **Raspberry Pi 4 / 5** : Overwitch (JACK ou PipeWire) donne les canaux du Cycles comme des ports audio ; les voix
  (Plaits, Rings, Clouds, msfa/Dexed, DaisySP…, en virgule flottante) tournent à côté et renvoient leur son par les
  canaux depuis l'hôte. Les notes et les potards passent par le MIDI USB du Cycles (interfaces 4 et 5).
- **Pico 2** : non pour Overbridge (Full Speed seulement, §En bref). Possible en appareil MIDI (DIN ou USB) à côté,
  sans retour audio : le Cycles n'a pas d'entrée audio.

## 6. L'expérience sur le Cycles (22/10/2026) `[FAIT en émulation]`

Tweak `70-overbridge-exp.json` (`tools/gen_overbridge.py`, `tools/machines/overbridge/ob.S`, preuve
`tools/emu/test_overbridge.py`). **Hors de l'OS** : il prend les mêmes appels que l'envoi USB à heure fixe, le 6 canaux et
Mod Settings, et des masques ou crochets de Model-TG, MACRO, ACID et des moteurs du Syntakt (`conflicts`). On le
construit seul sur l'OS d'origine : `build.py -t overbridge-exp`.

### 6.1 Ce que fait le tweak

- **Le mode 5 (USB AUDIO/MIDI)** devient Overbridge : entrée 4 de la table `0x4013e538` (activation `ob_act`, fin
  `ob_deact`, longueur de la configuration), PID `0x7ff0` dans le descripteur d'appareil `0x4019b3fe`, configuration de
  249 o écrite sur celle de l'audio USB (`0x4019b2b6`, 328 o) : IAD + interfaces vendor 0..3 (1 et 2 : alt 0..3, EP
  0x83 / 0x03 interrupt, 32 / 88 / **256** o, intervalle 1 ; 3 sans point d'accès), puis IAD + MIDI USB (4 contrôle,
  5 MIDI streaming, EP 0x01 / 0x81 bulk 512 o). Les modes USB MIDI et CDC (`CONFIG › UPGRADE`) ne changent pas.
- **Activation** : la fin d'origine (`0x40002c56`), la remise à zéro des réserves SRAM (`0x40002da8`), têtes de file des
  points d'accès 7 et 6 (max 1 024, pas de dTD), `ENDPTCTRL3 = 0x00cc00cc`, puis la partie MIDI d'origine
  (`0x4000311c`).
- **`SET_INTERFACE`** (`0x400047c6`, mode 5) : les numéros d'interface échangés (`0x400047ea`, `0x40004818`) et les
  quatre appels de démarrage / arrêt dirigés vers `ob_in_*` / `ob_out_*`.
- **Requêtes vendor** : `0x4000453c` (la fin de la chaîne de comparaisons du répartiteur `0x4000448c`) saute vers
  `ob_vendor` : `0xC0` 1 → « Model:Cycles » (32 o), `0xC0` 2 → « 0001        0.01 » (16 o) ; `0xA1` 1 continue vers
  `0x400048c6` comme avant, le reste est bloqué (stall) comme avant.
- **Le son** : `0x40059392` et `0x400593ce` (envoi du bloc, `0x40002912`) → `ob_write` ; `0x40059338` (lecture de
  l'entrée USB, `0x40002ae0`) → `ob_read`.
  - vers l'hôte : 8 canaux, Main L/R (`0x40fe4b90`) et les 6 pistes (`0x80001858`), en blocs de 7 trames ; ring de
    8 blocs ; un bloc dont le dTD est encore en file (l'hôte ne lit pas) est abandonné et compté, jamais réécrit ;
  - depuis l'hôte : 6 dTD de réception ; les blocs lus vont dans une FIFO de 256 trames ; un dTD n'est remis en
    file que si la FIFO a de la place (l'hôte attend, NAK) ; lecture après 64 trames de préremplissage ; canaux 1 et 2
    → le tampon de l'entrée USB (`0x40fe4cf0`), que l'OS mélange à sa sortie comme l'audio USB d'origine ; canaux 3..8
    reçus mais pas joués (pour un Sidecar).
- **Mémoire** : code dans 9 masques 48×22 libérés (1 296 o) ; blocs dans le trou au-dessus du BSS, par l'alias sans
  cache (`0x4a33a000`, 8 + 6 blocs de 256 o), FIFO et état en cache (`0x4233b000`, `0x4233d000`) ; dTD dans les
  réserves SRAM d'origine (`0x80008000`, `0x80008800`), libres dans ce mode.

### 6.2 Preuve (`test_overbridge.py`, 25 vérifications, TOUT OK)

- descripteurs : configuration parcourue jusqu'à sa longueur, alt 3 des interfaces 1 et 2 (256 o, interrupt,
  chaque micro-trame), MIDI en 4 et 5, PID ; taille de bloc = celle du fichier d'appareil d'Overwitch ;
- par le vrai répartiteur de l'OS : nom et version (et tronqués à `wLength`), `0xA1` 1 inchangé, autres requêtes
  vendor bloquées, `GET_DESCRIPTOR` rend les nôtres ;
- `SET_INTERFACE` : interface 1 = flux vers l'hôte, 2 = depuis l'hôte, marche / arrêt, 6 dTD en file, plus d'appel aux
  routines d'origine ;
- activation : `ENDPTCTRL3`, têtes de file, partie MIDI d'origine appelée avec son argument ;
- avec la vraie routine de mise en file de l'OS (`0x40003f94`) : dTD chaînés dans l'ordre sur le point d'accès 7 ;
- vers l'hôte, 3 000 blocs : chaque trame (Main L/R, pistes 1..6) une fois, dans l'ordre, compteur exact ; hôte
  arrêté 50 ms puis 2 ms : 351 blocs abandonnés, exactement ceux comptés, aucun bloc en file réécrit ;
- depuis l'hôte, 2 s : rafales de 24 blocs (comme Overwitch) à +50 et −80 ppm, hôte toujours prêt (FIFO bornée,
  219 trames), hôte arrêté 0,2 s : canaux 1 et 2 joués à partir de la trame 0, sans trou ni répétition, silence pendant
  l'arrêt puis reprise ;
- sans flux : rien n'est envoyé, l'entrée est silencieuse (comme l'OS d'origine sans hôte).
- Coût : `ob_write` ~1 450 instructions par bloc, `ob_read` ~1 400, flux actifs (environ 3 % d'un bloc).

### 6.3 À essayer sur la machine `[À FAIRE]`

Firmware construit à part (jamais publié) ; un Linux ou un Raspberry Pi avec Overwitch et le fichier d'appareil
(`gen_overbridge.py --overwitch ~/.config/overwitch/devices.d/model-cycles.json`). Ce que l'émulation ne peut pas
dire : si le contrôleur traite les points d'accès interrupt comme prévu, si l'hôte accepte la configuration,
la latence et la charge réelles. Retour à l'OS d'origine : menu de démarrage (MIDI IN) ou `CONFIG › UPGRADE` par USB.
