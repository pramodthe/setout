"""A sliding door threshold at a balcony, as the owner described it, written by hand as a target.

Every size below is an assumption: the drawing is not to scale. This shows what the output could be.
It says nothing yet about whether a model can write it.
"""
from blender_kit import *  # noqa: F403

start()
materials()

# ---- named values, mm. y runs from outside (negative) to inside; x runs along the opening from its middle
HALF_OPENING = 1200    # the section cut sits at the opening's middle, x = 0
WALL_END = 1880
DOOR_TOP = 2100
HEAD_TOP = 2180
WALL_TOP = 2520
SLAB_DEEP = 260
SLAB_OUT = -760        # how far the balcony runs
SLAB_IN = 760
STEP = 130             # slab top inside, above slab top on the balcony
OUT_FINISH = 72        # balcony bed and tile above the membrane
IN_FINISH = 66         # internal bed and tile
MEMBRANE = 5
PACKER = 7
SUB_T = 3
SUB_Z = MEMBRANE + PACKER
SUB_IN = 168           # inside edge of the sub-sill
UPSTAND = 112
ANGLE_Y = SUB_IN + 12
ANGLE_UP = IN_FINISH
ANGLE_LEG = 62
ANGLE_T = 4
JAMB_X = HALF_OPENING
SPACING = 400
WALL_Y0, WALL_Y1 = -52, 206

# ---- slab and step, with its top edge eased and its foot filleted under the membrane
slab = [(SLAB_OUT, -STEP - SLAB_DEEP), (SLAB_IN, -STEP - SLAB_DEEP), (SLAB_IN, 0), (22, 0), (0, -22), (0, -STEP), (SLAB_OUT, -STEP)]
prism("slab", slab, 0, WALL_END, "concrete", "slab")
prism("fillet", [(0, -STEP), (0, -STEP + 34), (-34, -STEP)], 0, JAMB_X, "bed", "fillet")

# ---- waterstop angle across the opening, returned towards the outside at the jamb
angle = [(ANGLE_Y + ANGLE_LEG, 0), (ANGLE_Y, 0), (ANGLE_Y, ANGLE_UP)]
sheet("waterstop_angle", angle, ANGLE_T, 0, JAMB_X, "galvanised", "waterstop")
box("waterstop_return", (JAMB_X - ANGLE_T, -8, 0), (JAMB_X, ANGLE_Y, ANGLE_UP), "galvanised", "waterstop")
x = SPACING / 2
while x < JAMB_X:
    screw("waterstop_screw", (x, ANGLE_Y + 40, ANGLE_T + 1), (x, ANGLE_Y + 40, -52), 2.6, "steel", "waterstop_fix")
    x += SPACING

# ---- membrane: one sheet from the balcony, up the step, under the sub-sill, up the angle
path = [(SLAB_OUT, -STEP), (-34, -STEP), (0, -STEP + 34), (0, -22), (22, 0), (ANGLE_Y - MEMBRANE, 0), (ANGLE_Y - MEMBRANE, ANGLE_UP + 6)]
sheet("membrane", path, MEMBRANE, 0, JAMB_X, "membrane", "membrane")
# dressed up the side of the wall at the jamb
box("membrane_jamb_side", (JAMB_X, WALL_Y0, -STEP), (JAMB_X + 4, ANGLE_Y, ANGLE_UP + 30), "membrane", "membrane")

# ---- packers: separate pieces under the sub-sill
x = 120
while x < JAMB_X - 40:
    box("packer", (x - 45, 22, MEMBRANE), (x + 45, 152, SUB_Z), "packer", "packers")
    x += 400

# ---- sub-sill: a folded tray with a drip outside and an upstand inside, closed by an end dam
sub = [(-34, SUB_Z - 30), (-22, SUB_Z - 16), (-22, SUB_Z), (SUB_IN, SUB_Z), (SUB_IN, UPSTAND)]
sheet("sub_sill", sub, SUB_T, 0, JAMB_X - 6, "aluminium", "sub_sill")
box("sub_sill_end_dam", (JAMB_X - 8, -20, SUB_Z), (JAMB_X - 5, SUB_IN, 66), "aluminium", "sub_sill")

# fixings through the tray, packer and membrane into the slab, each head under a dome of sealant
x = 200
while x < JAMB_X:
    screw("sub_sill_screw", (x, 92, SUB_Z + SUB_T + 2), (x, 92, -78), 3.0, "steel", "fix")
    lathe("oversealing", [(14, 0), (12.5, 4), (9, 8), (4, 10.5), (0.05, 11)], (x, 92, SUB_Z + SUB_T), (0, 0, 1), "sealant", "fix")
    x += 600

# ---- sill: the door's own extrusion, with legs and feet on the tray, and tracks for the panels
FOOT = SUB_Z + SUB_T
sill = [(8, FOOT), (40, FOOT), (40, FOOT + 8), (28, FOOT + 8), (28, FOOT + 26), (66, FOOT + 26), (66, FOOT + 62), (72, FOOT + 62),
        (72, FOOT + 26), (108, FOOT + 26), (108, FOOT + 62), (114, FOOT + 62), (114, FOOT + 26), (150, FOOT + 26),
        (150, FOOT + 8), (138, FOOT + 8), (138, FOOT), (160, FOOT), (160, FOOT + 80), (154, FOOT + 80), (154, FOOT + 32),
        (22, FOOT + 32), (22, FOOT + 14), (8, FOOT + 14)]
prism("sill", sill, 0, JAMB_X - 10, "frame", "frame")

# ---- frame: jamb and head of the same section, and the panels in their tracks
box("jamb_outer", (JAMB_X - 10, 8, FOOT), (JAMB_X, 160, HEAD_TOP), "frame", "frame")
box("jamb_web", (JAMB_X - 44, 66, FOOT + 62), (JAMB_X - 10, 72, DOOR_TOP), "frame", "frame")
box("jamb_web", (JAMB_X - 44, 108, FOOT + 62), (JAMB_X - 10, 114, DOOR_TOP), "frame", "frame")
prism("head", [(8, HEAD_TOP), (160, HEAD_TOP), (160, DOOR_TOP - 16), (154, DOOR_TOP - 16), (154, HEAD_TOP - 8), (114, HEAD_TOP - 8), (114, DOOR_TOP - 34),
               (108, DOOR_TOP - 34), (108, HEAD_TOP - 8), (72, HEAD_TOP - 8), (72, DOOR_TOP - 34), (66, DOOR_TOP - 34), (66, HEAD_TOP - 8),
               (14, HEAD_TOP - 8), (14, DOOR_TOP - 16), (8, DOOR_TOP - 16)], 0, JAMB_X, "frame", "frame")


def panel(name, y0, y1, x0, x1, group):
    stile, rail = 58, 76
    z0, z1 = FOOT + 40, DOOR_TOP - 26
    box(f"{name}_rail", (x0, y0, z0), (x1, y1, z0 + rail), "frame", group)
    box(f"{name}_rail", (x0, y0, z1 - 62), (x1, y1, z1), "frame", group)
    box(f"{name}_stile", (x1 - stile, y0, z0 + rail), (x1, y1, z1 - 62), "frame", group)
    middle = (y0 + y1) / 2
    box(f"{name}_glass", (x0, middle - 11, z0 + rail - 8), (x1 - stile + 8, middle + 11, z1 - 54), "glass", group)
    for x in (x1 - 150, max(x0 + 150, x1 - 760)):
        lathe(f"{name}_roller", [(0.05, -6), (16, -6), (17, -3), (17, 3), (16, 6), (0.05, 6)], (x, middle, z0 - 12), (0, 1, 0), "rubber", group)


panel("fixed_panel", 76, 104, 0, JAMB_X - 12, "panels")
panel("sliding_panel", 34, 62, 0, JAMB_X - 12, "panels")

# ---- seals: backing rod first, silicone over it, in both joints along the run
rod("backing_rod", (0, 164, UPSTAND - 22), (JAMB_X - 12, 164, UPSTAND - 22), 5.5, "backing", "seals")
prism("silicone_seal", [(160, UPSTAND - 16), (167, UPSTAND - 16), (168, UPSTAND + 2), (160, UPSTAND + 6)], 0, JAMB_X - 12, "silicone", "seals")
rod("backing_rod", (0, SUB_IN + 7, ANGLE_UP - 14), (JAMB_X - 12, SUB_IN + 7, ANGLE_UP - 14), 4.5, "backing", "seals")
prism("silicone_seal", [(SUB_IN + SUB_T, ANGLE_UP - 8), (ANGLE_Y - MEMBRANE, ANGLE_UP - 8), (ANGLE_Y - 2, ANGLE_UP + 12), (SUB_IN + SUB_T, ANGLE_UP + 16)], 0, JAMB_X - 12, "silicone", "seals")

# ---- wall: two brick leaves past the jamb and over the head, as nothing else is drawn
bricks = brick_wall("outer_leaf", JAMB_X + 4, WALL_END, WALL_Y0, WALL_Y0 + 110, -STEP, WALL_TOP, "brick", "mortar", "wall")
bricks += brick_wall("inner_leaf", JAMB_X + 4, WALL_END, WALL_Y1 - 110, WALL_Y1, 0, WALL_TOP, "brick", "mortar", "wall")
box("lintel", (0, WALL_Y0, HEAD_TOP + 2), (JAMB_X + 230, WALL_Y0 + 110, HEAD_TOP + 8), "steel", "wall")
bricks += brick_wall("over_head_outer", 0, JAMB_X + 4, WALL_Y0, WALL_Y0 + 110, HEAD_TOP + 8, WALL_TOP, "brick", "mortar", "wall")
bricks += brick_wall("over_head_inner", 0, JAMB_X + 4, WALL_Y1 - 110, WALL_Y1, HEAD_TOP + 8, WALL_TOP, "brick", "mortar", "wall")

# ---- finishes: a bed and real tiles each side
box("balcony_bed", (0, SLAB_OUT, -STEP + MEMBRANE), (JAMB_X, -6, -STEP + OUT_FINISH - 12), "bed", "balcony_finish")
tiles("balcony_tile", 0, JAMB_X, SLAB_OUT, -8, -STEP + OUT_FINISH - 12, -STEP + OUT_FINISH, 300, 4, "tile", "balcony_finish")
box("floor_bed", (0, ANGLE_Y + ANGLE_LEG + 4, 2), (WALL_END, SLAB_IN, IN_FINISH - 12), "bed", "floor_finish")
tiles("floor_tile", 0, WALL_END, ANGLE_Y + 8, SLAB_IN, IN_FINISH - 12, IN_FINISH, 300, 4, "tile", "floor_finish")

print("parts", len(PARTS), tally())

stage()
shot("section.png", (-2600, 60, 40), (0, 60, 40), size=(1400, 1000), samples=32, ortho=1050)
shot("cut.png", (-3300, -2500, 1900), (640, 40, 820), lens=42, samples=40)
explode({"fillet": (0, 0, 60), "waterstop": (0, 0, 170), "waterstop_fix": (0, 0, 280), "membrane": (0, 0, 380), "packers": (0, 0, 520), "sub_sill": (0, 0, 660), "fix": (0, 0, 860),
         "frame": (0, 0, 1040), "panels": (0, 0, 1040), "seals": (0, 0, 980), "wall": (900, 0, 0), "balcony_finish": (0, -500, 0), "floor_finish": (0, 620, 0)})
shot("exploded.png", (-4300, -3300, 2500), (760, 40, 1250), lens=42, samples=40)
settle()
export_glb("model.glb")
