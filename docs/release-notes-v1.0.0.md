# v1.0.0

First public release of the FRLG+ save extractor.

## What it decodes

Trainer and rival name, the Key System settings (version toggle, difficulty, Nuzlocke,
IV/EV calculation mode, No Free Heals, Exp. modifier), badges, Pokédex and Extended Dex
progress, the party and all 14 PC boxes with IVs, EVs, natures, moves and origins, the
fully rebuilt FRLG+ bag (all seven pockets), both Day Cares, the roaming legendary, Master
Trainer progress, event flags and story variables, game stats, the Hall of Fame, and a diff
against a previous run. It only ever reads the save file.

## Installing

Download the attached `frlg-save-extractor.skill` file from this release and install it as a
Claude skill.

## Provenance

The save-layout and name tables are generated from
[Deokishisu/FRLG-Plus](https://github.com/Deokishisu/FRLG-Plus), itself based on
[pret/pokefirered](https://github.com/pret/pokefirered). No ROM, patch or game asset is
included.
