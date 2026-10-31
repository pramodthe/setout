"""Hand-authored reference script for the floor-waste junction prompt.

The harness execs ``SCRIPT``. Running this module does not build a scene.
Dimensions are the ones the prompt asks for (100 mm upturn) plus illustrative
sizes for the parts the prompt names. They are not a cited rule or a product.
"""

SCRIPT = r'''
PARAMS = {
    "substrate_thk": 100,
    "screed_thk": 40,
    "membrane_thk": 1.5,
    "upturn_h": 100,
    "angle_leg": 40,
    "angle_thk": 1.5,
    "tile_thk": 10,
    "sealant_w": 8,
    "run_x": 400,
    "run_y": 240,
}

for _part, _over in (OVERRIDES or {}).items():
    for _key, _value in (_over.get("dims") or {}).items():
        if _key in PARAMS:
            PARAMS[_key] = _value

SCENE_NAME = "FloorWasteJunction"

def dim(name):
    return PARAMS[name]

z_sub = dim("substrate_thk")
z_scr = z_sub + dim("screed_thk")
z_mem = z_scr + dim("membrane_thk")
leg = dim("angle_leg")
ath = dim("angle_thk")
gap = dim("sealant_w")
run_x = dim("run_x")
run_y = dim("run_y")
# The angle sits on the room side of the upturn so the two skins do not occupy
# the same volume (coplanar faces shimmer in the preview).
face = dim("membrane_thk")

box_mm(
    "Substrate", (run_x, run_y, dim("substrate_thk")), (0, 0, 0),
    collection="Structure", dims={"substrate_thk": dim("substrate_thk")},
    material="Concrete", build_order=0, color=(0.45, 0.44, 0.42, 1),
)
box_mm(
    "Screed", (run_x, run_y, dim("screed_thk")), (0, 0, z_sub),
    collection="Structure", dims={"screed_thk": dim("screed_thk")},
    material="Screed", build_order=1, color=(0.72, 0.62, 0.45, 1),
)
box_mm(
    "Membrane", (run_x, run_y, dim("membrane_thk")), (0, 0, z_scr),
    collection="Waterproofing", dims={"membrane_thk": dim("membrane_thk")},
    material="Membrane", build_order=2, color=(0.15, 0.32, 0.72, 1),
)
box_mm(
    "Upturn", (dim("membrane_thk"), run_y, dim("upturn_h")), (0, 0, z_scr),
    collection="Waterproofing",
    dims={"upturn_h": dim("upturn_h"), "membrane_thk": dim("membrane_thk")},
    material="Upturn", build_order=3, color=(0.10, 0.22, 0.55, 1),
)
extrude_profile(
    "Stainless Angle",
    [
        (face, z_mem),
        (face + leg, z_mem),
        (face + leg, z_mem + ath),
        (face + ath, z_mem + ath),
        (face + ath, z_mem + leg),
        (face, z_mem + leg),
    ],
    run_y,
    collection="Waterproofing",
    dims={"angle_leg": leg, "angle_thk": ath},
    material="Stainless", build_order=4,
    color=(0.62, 0.66, 0.70, 1), metallic=0.0, roughness=0.35,
)
box_mm(
    "Sealant", (gap, run_y, dim("tile_thk")), (face + leg, 0, z_mem),
    collection="Finishes", dims={"sealant_w": gap},
    material="Sealant", build_order=6, color=(0.16, 0.16, 0.17, 1),
)
box_mm(
    "Tile", (run_x - face - leg - gap, run_y, dim("tile_thk")), (face + leg + gap, 0, z_mem),
    collection="Finishes", dims={"tile_thk": dim("tile_thk")},
    material="Tile", build_order=5, color=(0.80, 0.74, 0.64, 1),
)
'''
