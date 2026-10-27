window.SCENE = {
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
      "note": "Structural substrate, bathroom side",
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
      "note": "Wall substrate behind membrane",
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
      "note": "Bond breaker at wall/floor junction and all internal corners",
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
          "x": 50,
          "y": 0,
          "rule_id": "NCC-10.2-hob-membrane-return",
          "label": "Terminate 50 mm onto the floor"
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
      "note": "Screed to falls over the membrane",
      "x": -900,
      "y": -21,
      "z": 0,
      "w": 810,
      "h": 22,
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
      "value": 50,
      "rule_id": "NCC-10.2-hob-membrane-return",
      "satisfied": true
    },
    {
      "parameter": "membrane_above_finished_tile_mm",
      "value": 1800,
      "rule_id": "NCC-10.2-membrane-wall-height",
      "satisfied": true
    }
  ],
  "rules_used": {
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
};