window.SCENES_VIOLATE = {
  "shower_hob": {
    "detail_id": "shower_hob",
    "title": "Shower hob \u2014 waterproofing membrane",
    "units": "mm",
    "layer_order": [
      "substrate",
      "primer",
      "bond_breaker",
      "membrane",
      "screed"
    ],
    "solids": [
      {
        "name": "Bathroom floor substrate",
        "layer": "substrate",
        "kind": "box",
        "color": "#8d8d8d",
        "rule_id": null,
        "note": "Structural substrate, bathroom side (product dimension, no governing rule)",
        "x": 0,
        "y": -60,
        "z": 0,
        "w": 700,
        "h": 60,
        "d": 1200
      },
      {
        "name": "Shower floor substrate",
        "layer": "substrate",
        "kind": "box",
        "color": "#8d8d8d",
        "rule_id": "NCC-10.2-shower-stepdown",
        "note": "Set down 25 mm below finished floor level",
        "x": -900,
        "y": -85,
        "z": 0,
        "w": 900,
        "h": 60,
        "d": 1200
      },
      {
        "name": "Shower wall substrate",
        "layer": "substrate",
        "kind": "box",
        "color": "#9a9a9a",
        "rule_id": null,
        "note": "Wall substrate behind membrane (product dimension, no governing rule)",
        "x": -960,
        "y": -25,
        "z": 0,
        "w": 60,
        "h": 2000,
        "d": 1200
      },
      {
        "name": "Hob",
        "layer": "substrate",
        "kind": "box",
        "color": "#a8a093",
        "rule_id": null,
        "note": "90 x 100 mm hob (product dimension)",
        "x": -90,
        "y": 0,
        "z": 0,
        "w": 90,
        "h": 100,
        "d": 1200
      },
      {
        "name": "Primer",
        "layer": "primer",
        "kind": "coat",
        "color": "#d8c48a",
        "rule_id": null,
        "note": "Substrate primer, per manufacturer manual",
        "follows": "membrane",
        "offset": -1
      },
      {
        "name": "Bond breaker",
        "layer": "bond_breaker",
        "kind": "coat",
        "color": "#c98f4a",
        "rule_id": null,
        "note": "Bond breaker at wall/floor junction and all internal corners (product dimension, no governing rule)",
        "x": -900,
        "y": -25,
        "z": 0,
        "w": 12,
        "h": 12,
        "d": 1200
      },
      {
        "name": "Waterproofing membrane",
        "layer": "membrane",
        "kind": "polyline",
        "color": "#2f6fb8",
        "rule_id": "NCC-10.2-hob-membrane-return",
        "note": "Continuous membrane. Each vertex is placed by a rule.",
        "path": [
          {
            "x": -900,
            "y": 1775,
            "rule_id": "NCC-10.2-membrane-wall-height",
            "label": "Membrane 1800 mm up the wall"
          },
          {
            "x": -900,
            "y": -25,
            "rule_id": "NCC-10.2-membrane-wall-height",
            "label": "Down the wall to the shower floor"
          },
          {
            "x": -90,
            "y": -25,
            "rule_id": "NCC-10.2-shower-stepdown",
            "label": "Across the shower floor, 25 mm below FFL"
          },
          {
            "x": -90,
            "y": 100,
            "rule_id": "NCC-10.2-hob-membrane-return",
            "label": "Up the inside face of the hob"
          },
          {
            "x": 0,
            "y": 100,
            "rule_id": "NCC-10.2-hob-membrane-return",
            "label": "Over the top of the hob"
          },
          {
            "x": 0,
            "y": 0,
            "rule_id": "NCC-10.2-hob-membrane-return",
            "label": "Down the outside face"
          },
          {
            "x": 20,
            "y": 0,
            "rule_id": "NCC-10.2-hob-membrane-return",
            "label": "Terminate 20 mm onto the floor"
          }
        ],
        "thickness": 4,
        "d": 1200
      },
      {
        "name": "Screed",
        "layer": "screed",
        "kind": "box",
        "color": "#bdb6a8",
        "rule_id": null,
        "note": "Screed to falls over the membrane (product dimension, no governing rule)",
        "x": -900,
        "y": -21,
        "z": 0,
        "w": 810,
        "h": 22,
        "d": 1200
      },
      {
        "name": "Bathroom floor tiles",
        "layer": "screed",
        "kind": "box",
        "color": "#cfc9bd",
        "rule_id": null,
        "note": "Floor tile finish (product dimension, no governing rule)",
        "x": 0,
        "y": 4,
        "z": 0,
        "w": 700,
        "h": 8,
        "d": 1200
      }
    ],
    "checks": [
      {
        "parameter": "stepdown_below_ffl_mm",
        "value": 25,
        "rule_id": "NCC-10.2-shower-stepdown",
        "satisfied": true
      },
      {
        "parameter": "membrane_return_onto_floor_mm",
        "value": 20,
        "rule_id": "NCC-10.2-hob-membrane-return",
        "satisfied": false
      },
      {
        "parameter": "membrane_above_finished_tile_mm",
        "value": 1800,
        "rule_id": "NCC-10.2-membrane-wall-height",
        "satisfied": true
      }
    ],
    "rules_used": {
      "NCC-10.2-hob-membrane-return": {
        "id": "NCC-10.2-hob-membrane-return",
        "source": "NCC2022-HP-10.2",
        "applies_to": "shower_hob",
        "parameter": "membrane_return_onto_floor_mm",
        "operator": ">=",
        "value": 50,
        "unit": "mm",
        "text": "For showers with hobs, the membrane must pass over the top of the hob, down the outside face, and terminate at least 50 mm onto the floor.",
        "drives_geometry": true
      },
      "NCC-10.2-membrane-wall-height": {
        "id": "NCC-10.2-membrane-wall-height",
        "source": "NCC2022-HP-10.2",
        "applies_to": "shower_wall",
        "parameter": "membrane_above_finished_tile_mm",
        "operator": ">=",
        "value": 1800,
        "unit": "mm",
        "text": "For hobless showers and showers with hobs or stepdowns, the membrane must run up the wall to at least 1800 mm above the finished tile level of the floor.",
        "drives_geometry": true
      },
      "NCC-10.2-shower-stepdown": {
        "id": "NCC-10.2-shower-stepdown",
        "source": "NCC2022-HP-10.2",
        "applies_to": "shower_floor",
        "parameter": "stepdown_below_ffl_mm",
        "operator": ">=",
        "value": 25,
        "unit": "mm",
        "text": "The shower area floor must be at least 25 mm below the finished floor level outside the shower.",
        "drives_geometry": true
      }
    }
  },
  "wall_floor_junction": {
    "detail_id": "wall_floor_junction",
    "title": "Wall/floor junction \u2014 flashing",
    "units": "mm",
    "layer_order": [
      "substrate",
      "membrane",
      "screed"
    ],
    "solids": [
      {
        "name": "Floor substrate",
        "layer": "substrate",
        "kind": "box",
        "color": "#8d8d8d",
        "rule_id": null,
        "note": "Structural substrate (product dimension, no governing rule)",
        "x": 0,
        "y": -60,
        "z": 0,
        "w": 600,
        "h": 60,
        "d": 1200
      },
      {
        "name": "Wall substrate",
        "layer": "substrate",
        "kind": "box",
        "color": "#9a9a9a",
        "rule_id": null,
        "note": "Wall substrate (product dimension, no governing rule)",
        "x": -60,
        "y": 0,
        "z": 0,
        "w": 60,
        "h": 400,
        "d": 1200
      },
      {
        "name": "Flashing vertical leg",
        "layer": "membrane",
        "kind": "box",
        "color": "#2f6fb8",
        "rule_id": "NCC-10.2-wallfloor-vertical-leg",
        "note": "Flashing up the wall 25 mm above FFL",
        "x": -6,
        "y": 0,
        "z": 0,
        "w": 6,
        "h": 25,
        "d": 1200
      },
      {
        "name": "Flashing horizontal leg",
        "layer": "membrane",
        "kind": "box",
        "color": "#2f6fb8",
        "rule_id": "NCC-10.2-wallfloor-horizontal-leg",
        "note": "Flashing 20 mm out onto the floor",
        "x": 0,
        "y": 0,
        "z": 0,
        "w": 20,
        "h": 6,
        "d": 1200
      },
      {
        "name": "Floor tiles",
        "layer": "screed",
        "kind": "box",
        "color": "#cfc9bd",
        "rule_id": null,
        "note": "Floor tile finish (product dimension, no governing rule)",
        "x": 20,
        "y": 0,
        "z": 0,
        "w": 580,
        "h": 8,
        "d": 1200
      }
    ],
    "checks": [
      {
        "parameter": "flashing_vertical_leg_above_ffl_mm",
        "value": 25,
        "rule_id": "NCC-10.2-wallfloor-vertical-leg",
        "satisfied": true
      },
      {
        "parameter": "flashing_horizontal_leg_mm",
        "value": 20,
        "rule_id": "NCC-10.2-wallfloor-horizontal-leg",
        "satisfied": false
      }
    ],
    "rules_used": {
      "NCC-10.2-wallfloor-horizontal-leg": {
        "id": "NCC-10.2-wallfloor-horizontal-leg",
        "source": "NCC2022-HP-10.2",
        "applies_to": "wall_floor_junction",
        "parameter": "flashing_horizontal_leg_mm",
        "operator": ">=",
        "value": 50,
        "unit": "mm",
        "text": "Wall/floor flashing horizontal leg must be at least 50 mm wide.",
        "drives_geometry": true
      },
      "NCC-10.2-wallfloor-vertical-leg": {
        "id": "NCC-10.2-wallfloor-vertical-leg",
        "source": "NCC2022-HP-10.2",
        "applies_to": "wall_floor_junction",
        "parameter": "flashing_vertical_leg_above_ffl_mm",
        "operator": ">=",
        "value": 25,
        "unit": "mm",
        "text": "Wall/floor flashing vertical leg must be at least 25 mm above finished floor level, except across doorways.",
        "drives_geometry": true
      }
    }
  },
  "internal_corner": {
    "detail_id": "internal_corner",
    "title": "Internal corner \u2014 flashing",
    "units": "mm",
    "layer_order": [
      "substrate",
      "bond_breaker",
      "membrane"
    ],
    "solids": [
      {
        "name": "Floor substrate",
        "layer": "substrate",
        "kind": "box",
        "color": "#8d8d8d",
        "rule_id": null,
        "note": "Structural substrate (product dimension, no governing rule)",
        "x": 0,
        "y": -60,
        "z": 0,
        "w": 500,
        "h": 60,
        "d": 500
      },
      {
        "name": "Wall A substrate",
        "layer": "substrate",
        "kind": "box",
        "color": "#9a9a9a",
        "rule_id": null,
        "note": "Wall substrate (product dimension, no governing rule)",
        "x": 0,
        "y": 0,
        "z": -60,
        "w": 500,
        "h": 1900,
        "d": 60
      },
      {
        "name": "Wall B substrate",
        "layer": "substrate",
        "kind": "box",
        "color": "#9a9a9a",
        "rule_id": null,
        "note": "Wall substrate (product dimension, no governing rule)",
        "x": -60,
        "y": 0,
        "z": 0,
        "w": 60,
        "h": 1900,
        "d": 500
      },
      {
        "name": "Bond breaker",
        "layer": "bond_breaker",
        "kind": "box",
        "color": "#c98f4a",
        "rule_id": null,
        "note": "Bond breaker fillet up the corner (product dimension, no governing rule)",
        "x": 0,
        "y": 0,
        "z": 0,
        "w": 12,
        "h": 1800,
        "d": 12
      },
      {
        "name": "Flashing leg on wall A",
        "layer": "membrane",
        "kind": "box",
        "color": "#2f6fb8",
        "rule_id": "NCC-10.2-internal-flashing-overlap",
        "note": "Flashing 20 mm out from the corner, 1800 mm up from FFL",
        "x": 0,
        "y": 0,
        "z": 0,
        "w": 20,
        "h": 1800,
        "d": 6
      },
      {
        "name": "Flashing leg on wall B",
        "layer": "membrane",
        "kind": "box",
        "color": "#2f6fb8",
        "rule_id": "NCC-10.2-internal-flashing-overlap",
        "note": "Flashing 20 mm out from the corner, 1800 mm up from FFL",
        "x": 0,
        "y": 0,
        "z": 0,
        "w": 6,
        "h": 1800,
        "d": 20
      }
    ],
    "checks": [
      {
        "parameter": "flashing_leg_overlap_mm",
        "value": 20,
        "rule_id": "NCC-10.2-internal-flashing-overlap",
        "satisfied": false
      },
      {
        "parameter": "vertical_flashing_above_ffl_mm",
        "value": 1800,
        "rule_id": "NCC-10.2-vertical-flashing-height",
        "satisfied": true
      }
    ],
    "rules_used": {
      "NCC-10.2-internal-flashing-overlap": {
        "id": "NCC-10.2-internal-flashing-overlap",
        "source": "NCC2022-HP-10.2",
        "applies_to": "internal_corner",
        "parameter": "flashing_leg_overlap_mm",
        "operator": ">=",
        "value": 40,
        "unit": "mm",
        "text": "Internal flashings must have each leg overlapping at least 40 mm.",
        "drives_geometry": true
      },
      "NCC-10.2-vertical-flashing-height": {
        "id": "NCC-10.2-vertical-flashing-height",
        "source": "NCC2022-HP-10.2",
        "applies_to": "shower_wall",
        "parameter": "vertical_flashing_above_ffl_mm",
        "operator": ">=",
        "value": 1800,
        "unit": "mm",
        "text": "Vertical flashings must extend at least 1800 mm above finished floor level.",
        "drives_geometry": true
      }
    }
  }
};