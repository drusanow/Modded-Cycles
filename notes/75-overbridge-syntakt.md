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
