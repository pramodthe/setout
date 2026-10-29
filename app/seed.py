"""Seed specification for the primary detail.

D3 = "STANDARD SILL SECTION DOOR OPENING" (Conpro technical-tips sheet,
page 1, embedded figure p0_Image21, 781x615 px).

ORIGIN: human transcription (28 Oct 2026) from the drawing crops read
directly. This is a documented seed, NOT model output and NOT a fresh
generation. Every value carries provenance; exact drawing labels are quoted
in `source_label`; anything without a drawing number is an ASSUMPTION and is
excluded from verified-dimension counts.

Section plane: +x toward exterior, +y up, origin at interior FFL on the
step face. +z along the sill (illustrative extent only).
"""

from __future__ import annotations

from .spec import (
    Component,
    DetailSpec,
    GeneratorRef,
    Param,
    ParamRole,
    PathPoint,
    ProductRef,
    Provenance,
    Relation,
    SourceDoc,
    SourceRegion,
    Unresolved,
)

CONPRO_SHA256 = "f1cc2f45609b28e1ed66fb74c680f3dd0992ded800059249bd4a6a30d1764239"
CONPRO_FILENAME = "TECHNICAL-TIPS-Door-and-Window-Sills-waterproofing.pdf"
CROP = "p0_Image21"
GEN = "setout-mesh-v0"

D = Provenance.DRAWING
A = Provenance.ASSUMPTION


def _p(name, value, prov, unit="mm", role=ParamRole.PHYSICAL, quote=None,
       tol=None, note=None) -> Param:
    return Param(name=name, value=value, unit=unit, provenance=prov, role=role,
                 source_quote=quote, tolerance_mm=tol, note=note)


def _r(x0, y0, x1, y1) -> list[SourceRegion]:
    return [SourceRegion(crop_id=CROP, x0=x0, y0=y0, x1=x1, y1=y1,
                         kind="indicative")]


def door_sill_d3() -> DetailSpec:
    comps = [
        Component(
            id="slab_upper", type="substrate", source_label=None,
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", -320, A, note="datum: crop edge, not dimensioned"),
                    _p("x1", 0, A, note="datum: step face plane x=0"),
                    _p("top_y", 0, A, note="datum: interior FFL y=0"),
                    _p("thickness", 150, A, note="not dimensioned in D3")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.10, 0.42, 0.42, 0.97), color="#8d8d8d",
            note="Interior slab (left grey mass). Material not labelled; "
                 "assumed concrete substrate."),
        Component(
            id="slab_lower", type="substrate", source_label=None,
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", 0, A, note="datum: step face plane x=0"),
                    _p("x1", 560, A, note="crop edge, not dimensioned"),
                    _p("step_height", 100, D, quote="100mm minimum step",
                       tol=0.5, note="MINIMUM: built step must be >= 100"),
                    _p("thickness", 150, A, note="not dimensioned in D3")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.42, 0.72, 0.98, 0.97), color="#8d8d8d",
            note="Exterior slab; top is 100mm (min) below interior FFL."),
        Component(
            id="waterstop", type="waterstop",
            source_label="Water-stop with membrane attached",
            representation="solid",
            generator=GeneratorRef(id="l_angle", version=GEN),
            params=[_p("x_face", -272, A, note="position not dimensioned"),
                    _p("leg_h", 90, A), _p("leg_t", 3, A),
                    _p("foot_len", 40, A),
                    _p("base_y", 1.5, A, note="derived: membrane top")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.225, 0.24, 0.26, 0.42), color="#c0c6cc",
            install_after=["slab_upper"],
            note="Aluminium angle; membrane bonded to its interior face."),
        Component(
            id="membrane", type="membrane", source_label="Waterproof membrane",
            representation="sheet",
            generator=GeneratorRef(id="sheet_follow", version=GEN),
            params=[_p("thickness_physical", 1.5, A,
                       note="typical sheet; not dimensioned"),
                    _p("thickness_display", 4, A, role=ParamRole.DISPLAY,
                       note="exaggerated so the layer is visible")],
            path=[
                PathPoint(x=-272.75, y=91.5, provenance=A,
                          note="upstand height not dimensioned"),
                PathPoint(x=-272.75, y=0.75, provenance=A,
                          note="datum: interior FFL + half thickness"),
                PathPoint(x=0, y=0.75, provenance=A, note="datum run"),
                PathPoint(x=0.75, y=0.75, provenance=A,
                          note="datum: wraps step edge"),
                PathPoint(x=0.75, y=-99.25, provenance=D,
                          note="down the step face; depth from 100mm step"),
                PathPoint(x=520, y=-99.25, provenance=A,
                          note="runs past crop edge; termination unresolved"),
            ],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.40, 0.40, 0.56, 0.75), color="#2f6fb8",
            install_after=["slab_upper", "slab_lower", "waterstop"],
            note="Continuous sheet: upstand, under sill, down step face, "
                 "along exterior slab. Centreline path; ribbon +/- t/2."),
        Component(
            id="protection_v", type="protection_sheet",
            source_label="Membrane protection sheet",
            representation="sheet",
            generator=GeneratorRef(id="sheet_follow", version=GEN),
            params=[_p("thickness_physical", 1.0, A),
                    _p("thickness_display", 2.5, A, role=ParamRole.DISPLAY)],
            path=[PathPoint(x=-274.75, y=91.5, provenance=A),
                  PathPoint(x=-274.75, y=0.75, provenance=A,
                            note="derived: alongside membrane upstand")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.20, 0.30, 0.225, 0.42), color="#cfd4d8",
            install_after=["membrane"]),
        Component(
            id="packer", type="packer", source_label="Sill packer",
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", -230, A), _p("x1", -120, A),
                    _p("y0", 1.5, A, note="derived: membrane top"),
                    _p("h", 8, A, note="packer height not dimensioned")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.38, 0.40, 0.50, 0.43), color="#d97b36",
            install_after=["membrane"]),
        Component(
            id="frame", type="door_frame", source_label=None,
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", -230, A), _p("x1", -120, A),
                    _p("y0", 9.5, A, note="derived: packer top"),
                    _p("h", 60, A, note="bounding box; exact extrusion "
                                        "profile not dimensioned")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.28, 0.20, 0.55, 0.42), color="#c9ced4",
            install_after=["packer"],
            note="Door sill section (white aluminium profile). Simplified "
                 "to bounding box; see unresolved U-frame-profile."),
        Component(
            id="drainage", type="drainage_holes",
            source_label="Drainage holes", representation="annotation",
            generator=GeneratorRef(id="annotation", version=GEN),
            params=[_p("x", -175, A, note="indicative position"),
                    _p("y", 72, A, note="indicative position")],
            source_regions=_r(0.40, 0.19, 0.52, 0.24), color="#555555",
            install_after=["frame"],
            note="Openings through frame head; holes not modelled in v0."),
        Component(
            id="cover_angle", type="cover_angle",
            source_label="Decorative cover angle",
            representation="solid",
            generator=GeneratorRef(id="l_angle", version=GEN),
            params=[_p("x_face", -239, A), _p("leg_h", 70, A),
                    _p("leg_t", 3, A), _p("foot_len", 30, A),
                    _p("base_y", 1.5, A, note="derived: membrane top")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.225, 0.20, 0.25, 0.24), color="#aab2b8",
            install_after=["membrane"]),
        Component(
            id="sealant", type="sealant_bead",
            source_label="6mm gap with backing rod and sealant",
            representation="solid",
            generator=GeneratorRef(id="bead", version=GEN),
            params=[_p("x0", -236, A, note="derived: cover angle face"),
                    _p("width", 6, D,
                       quote="6mm gap with backing rod and sealant", tol=0.5),
                    _p("y0", 20, A, note="bead extents not dimensioned"),
                    _p("y1", 70, A, note="bead extents not dimensioned")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.245, 0.20, 0.275, 0.26), color="#7fb539",
            install_after=["frame", "cover_angle"]),
        Component(
            id="rod", type="backing_rod",
            source_label="6mm gap with backing rod and sealant",
            representation="solid",
            generator=GeneratorRef(id="rod", version=GEN),
            params=[_p("cx", -233, A, note="derived: gap centre"),
                    _p("cy", 17, A), _p("r", 3, A,
                                        note="≈ half the 6mm gap")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.245, 0.24, 0.275, 0.28), color="#e0c341",
            install_after=["frame", "cover_angle"]),
        Component(
            id="joint", type="perimeter_joint",
            source_label="Compressible perimeter joint",
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", 1.5, A, note="derived: membrane face"),
                    _p("x1", 11.5, A, note="10mm joint, not dimensioned"),
                    _p("y0", -97.5, A, note="derived: screed bed"),
                    _p("y1", -8, A, note="top edge not dimensioned")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.50, 0.42, 0.56, 0.60), color="#6f6f6f",
            install_after=["slab_upper", "slab_lower"]),
        Component(
            id="fillet", type="fillet",
            source_label="Fillet or bond breaker",
            representation="solid",
            generator=GeneratorRef(id="fillet", version=GEN),
            params=[_p("x", 1.5, A, note="derived: membrane face at corner"),
                    _p("y", -97.5, A, note="derived: membrane top at corner"),
                    _p("leg_x", 20, A, note="cove size not dimensioned"),
                    _p("leg_y", 20, A, note="cove size not dimensioned")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.50, 0.68, 0.54, 0.73), color="#2b2b2b",
            install_after=["membrane", "joint"],
            note="Triangular cove at the base of the step face, on the membrane."),
        Component(
            id="slip_sheet", type="protection_sheet",
            source_label="Slip/protection sheet",
            representation="sheet",
            generator=GeneratorRef(id="sheet_follow", version=GEN),
            params=[_p("thickness_physical", 1.0, A),
                    _p("thickness_display", 2.5, A, role=ParamRole.DISPLAY)],
            path=[PathPoint(x=0.75, y=-98, provenance=A,
                            note="derived: on membrane top"),
                  PathPoint(x=520, y=-98, provenance=A,
                            note="runs past crop edge")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.55, 0.71, 0.98, 0.74), color="#dfe3e6",
            install_after=["membrane"]),
        Component(
            id="screed", type="screed", source_label="1:80 min. Fall to outlet",
            representation="solid",
            generator=GeneratorRef(id="wedge", version=GEN),
            params=[_p("x0", 21.5, A, note="derived: fillet toe"),
                    _p("x1", 520, A, note="crop edge"),
                    _p("y_bottom", -97.5, A, note="derived: slip sheet top"),
                    _p("y_top_x0", -40, A,
                       note="screed depth at step not dimensioned"),
                    _p("fall_ratio", 0.0125, D, unit="ratio",
                       quote="1:80 min. Fall to outlet", tol=0.0007,
                       note="MINIMUM fall toward +x outlet")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_r(0.55, 0.60, 0.98, 0.73), color="#bdb6a8",
            install_after=["slip_sheet", "joint", "fillet"],
            note="Screed to falls over the slip sheet; top falls 1:80 min."),
        Component(
            id="weep", type="weep_hole", source_label="Weep-hole",
            representation="annotation",
            generator=GeneratorRef(id="annotation", version=GEN),
            params=[_p("x", 30, A, note="indicative position"),
                    _p("y", -60, A, note="indicative position")],
            source_regions=_r(0.545, 0.585, 0.575, 0.61), color="#555555",
            install_after=["joint"],
            note="Opening through the joint; holes not modelled in v0."),
        Component(
            id="termination", type="termination_datum",
            source_label="Perimeter termination height of membrane",
            representation="annotation",
            generator=GeneratorRef(id="annotation", version=GEN),
            params=[_p("y", 60, A, note="indicative: dashed datum in crop; "
                                        "no value in drawing")],
            source_regions=_r(0.60, 0.30, 0.99, 0.33), color="#555555",
            note="Datum only: the drawing gives no height value (see U-term)."),
    ]
    relations = [
        Relation(source="frame", target="packer", kind="supported_by"),
        Relation(source="packer", target="membrane", kind="bonded_to"),
        Relation(source="membrane", target="slab_upper", kind="bonded_to"),
        Relation(source="membrane", target="slab_lower", kind="bonded_to"),
        Relation(source="joint", target="membrane", kind="laps_over"),
        Relation(source="slip_sheet", target="membrane", kind="laps_over"),
        Relation(source="screed", target="slip_sheet", kind="covers"),
        Relation(source="fillet", target="membrane", kind="bonded_to"),
        Relation(source="waterstop", target="membrane", kind="bonded_to"),
        Relation(source="sealant", target="frame", kind="sealed_to"),
        Relation(source="sealant", target="cover_angle", kind="sealed_to"),
        Relation(source="cover_angle", target="membrane", kind="bonded_to"),
        Relation(source="packer", target="frame", kind="installed_before"),
        Relation(source="membrane", target="packer", kind="installed_before"),
        Relation(source="membrane", target="slip_sheet", kind="installed_before"),
        Relation(source="slip_sheet", target="screed", kind="installed_before"),
        Relation(source="joint", target="screed", kind="installed_before"),
    ]
    unresolved = [
        Unresolved(id="U-membrane-termination",
                   description="Membrane run continues past the crop edge; "
                               "termination point/height unknown.",
                   impacts=["membrane", "slip_sheet", "screed"]),
        Unresolved(id="U-frame-profile",
                   description="Exact aluminium extrusion profile of the door "
                               "sill section is not dimensioned; built as a "
                               "bounding box.",
                   impacts=["frame"]),
        Unresolved(id="U-term-height",
                   description="'Perimeter termination height of membrane' is "
                               "a datum with no value in the drawing.",
                   impacts=["termination", "membrane"]),
        Unresolved(id="U-slab-dims",
                   description="Slab thicknesses and plan extents are not "
                               "dimensioned; assumed 150mm thick.",
                   impacts=["slab_upper", "slab_lower"]),
        Unresolved(id="U-extrusion",
                   description="A single section cannot fix 3D depth; 600mm "
                               "illustrative extent assumed and excluded "
                               "from accuracy checks.",
                   impacts=["build"]),
        Unresolved(id="U-products",
                   description="No manufacturer system selected; every "
                               "product ref is null. Conpro sheet is a "
                               "reference drawing, not a product approval.",
                   impacts=[c.id for c in comps if c.product is not None]),
        Unresolved(id="U-openings",
                   description="Drainage holes and weep-hole are annotation "
                               "positions only; openings not cut in v0.",
                   impacts=["drainage", "weep"]),
    ]
    return DetailSpec(
        detail_id="conpro-door-sill-D3",
        title="Standard sill section — door opening (D3)",
        origin="human-transcription-v1 (28 Oct 2026, embedded figure "
               "p0_Image21; documented seed, NOT model output)",
        source=SourceDoc(
            filename=CONPRO_FILENAME, sha256=CONPRO_SHA256, page_index=0,
            crop_id=CROP, page_width_pt=540, page_height_pt=780),
        context={
            "location": "door sill at slab edge with step down to exterior",
            "situation": ["Residential", "External", "Balcony / Doorway", "Door sill"],
            "substrate": "concrete slab (assumed; material not labelled)",
            "system": None,
            "illustrative_extent_z_mm": 600,
            "illustrative_extent_provenance": "assumption",
            "contact_checks": [
                ["packer", "membrane", "packer beds on membrane"],
                ["frame", "packer", "frame beds on packer"],
                ["fillet", "membrane", "fillet bonds to membrane"],
                ["screed", "slip_sheet", "screed covers slip sheet"],
            ],
            "note": "Conpro sheet is a reference drawing, not proof of any "
                    "Sika/modern system approval.",
        },
        components=comps, relations=relations, unresolved=unresolved,
    )


def door_subsill_d4() -> DetailSpec:
    """D4 = "SUB-SILL FIXING IN SLAB SET DOWN" (figure p0_Image22).

    Documented variant of the door-sill family on the SAME reference sheet:
    a rod-fixed sub-sill in an 80mm set-down, versus D3's packer-bedded
    standard sill at a 100mm (min) step. Different components (booker rod,
    CFC packers, grout, back nuts/washers), different parameters, different
    install dependencies — not a rename. Same origin caveats as D3.
    """
    CROPD = "p0_Image22"

    def _rd(x0, y0, x1, y1) -> list[SourceRegion]:
        return [SourceRegion(crop_id=CROPD, x0=x0, y0=y0, x1=x1, y1=y1,
                             kind="indicative")]

    comps = [
        Component(
            id="slab_upper", type="substrate", source_label=None,
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", -360, A), _p("x1", 0, A,
                                          note="datum: set-down face x=0"),
                    _p("top_y", 0, A, note="datum: interior FFL y=0"),
                    _p("thickness", 150, A)],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.10, 0.38, 0.33, 1.0), color="#8d8d8d"),
        Component(
            id="slab_lower", type="substrate", source_label=None,
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", 0, A), _p("x1", 420, A),
                    _p("step_height", 80, D,
                       quote="SUB-SILL SECTION FITTED INTO 80mm STEP DOWN",
                       tol=0.5),
                    _p("thickness", 150, A)],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.10, 0.56, 0.99, 1.0), color="#8d8d8d",
            note="Set-down bed 80mm below interior FFL."),
        Component(
            id="angle", type="edge_angle",
            source_label="Angle fitted to slab edge",
            representation="solid",
            generator=GeneratorRef(id="l_angle", version=GEN),
            params=[_p("x_face", -180, A), _p("leg_h", 50, A),
                    _p("leg_t", 3, A), _p("foot_len", 40, A),
                    _p("base_y", 1.5, A, note="derived: membrane top")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.33, 0.28, 0.37, 0.42), color="#c0c6cc",
            install_after=["slab_upper"]),
        Component(
            id="membrane", type="membrane",
            source_label="Membrane applied over angle",
            representation="sheet",
            generator=GeneratorRef(id="sheet_follow", version=GEN),
            params=[_p("thickness_physical", 1.5, A),
                    _p("thickness_display", 4, A, role=ParamRole.DISPLAY)],
            path=[
                PathPoint(x=-180.75, y=53, provenance=A,
                          note="over angle leg; height not dimensioned"),
                PathPoint(x=-180.75, y=0.75, provenance=A),
                PathPoint(x=0, y=0.75, provenance=A),
                PathPoint(x=0.75, y=0.75, provenance=A),
                PathPoint(x=0.75, y=-79.25, provenance=D,
                          note="down the set-down face; from 80mm step"),
                PathPoint(x=300, y=-79.25, provenance=A,
                          note="runs past crop edge; termination unresolved"),
            ],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.33, 0.28, 0.66, 0.60), color="#2f6fb8",
            install_after=["slab_upper", "slab_lower", "angle"]),
        Component(
            id="booker_rod", type="fixing_rod",
            source_label="Stainless steel booker rod set in chemical grout "
                         "shown green",
            representation="solid",
            generator=GeneratorRef(id="post", version=GEN),
            params=[_p("cx", 50, A), _p("cz", 300, A,
                                        note="mid-extent; spacing unknown"),
                    _p("r", 8, A, note="diameter not dimensioned"),
                    _p("y0", -230, A, note="embedment not dimensioned"),
                    _p("y1", -30, A, note="stick-up not dimensioned")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.43, 0.55, 0.48, 0.85), color="#9aa2ab",
            install_after=["slab_lower"],
            note="Vertical rod; threading not modelled. Passes through "
                 "packers, washer, nut and sub-sill (permitted "
                 "intersection: mechanical fixing)."),
        Component(
            id="grout", type="chemical_grout",
            source_label="Stainless steel booker rod set in chemical grout "
                         "shown green",
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", 30, A), _p("x1", 70, A),
                    _p("y0", -230, A), _p("y1", -80, A)],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.43, 0.55, 0.48, 0.85), color="#3fa34d",
            install_after=["slab_lower", "booker_rod"],
            note="Grout body around the embedded rod; colour per drawing."),
        Component(
            id="cfc_packers", type="packer",
            source_label="CFC packers under sub-sill section",
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", 20, A), _p("x1", 80, A),
                    _p("y0", -78.5, A, note="derived: membrane top"),
                    _p("h", 20, A, note="packer build-up not dimensioned")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.52, 0.38, 0.62, 0.55), color="#d97b36",
            install_after=["membrane"]),
        Component(
            id="washer", type="washer",
            source_label="Stainless steel back nuts and washers fitted to "
                         "booker rod and securing the sub-sill section in a "
                         "level position",
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", 30, A), _p("x1", 70, A),
                    _p("y0", -58.5, A, note="derived: packer top"),
                    _p("h", 4, A)],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.42, 0.36, 0.52, 0.48), color="#c9ced4",
            install_after=["cfc_packers", "booker_rod"]),
        Component(
            id="nut", type="nut",
            source_label="Stainless steel back nuts and washers fitted to "
                         "booker rod and securing the sub-sill section in a "
                         "level position",
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", 38, A), _p("x1", 62, A),
                    _p("y0", -54.5, A, note="derived: washer top"),
                    _p("h", 12, A)],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.42, 0.36, 0.52, 0.48), color="#8f979e",
            install_after=["washer", "booker_rod"]),
        Component(
            id="nut_patch", type="membrane",
            source_label="Membrane shown blue coated over booker rod nut "
                         "and washer",
            representation="sheet",
            generator=GeneratorRef(id="sheet_follow", version=GEN),
            params=[_p("thickness_physical", 1.5, A),
                    _p("thickness_display", 4, A, role=ParamRole.DISPLAY)],
            path=[PathPoint(x=30, y=-57, provenance=A),
                  PathPoint(x=30, y=-41, provenance=A),
                  PathPoint(x=70, y=-41, provenance=A,
                            note="coating draped over nut + washer"),
                  PathPoint(x=70, y=-57, provenance=A)],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.42, 0.38, 0.52, 0.50), color="#2f6fb8",
            install_after=["nut", "washer"]),
        Component(
            id="subsill", type="subsill",
            source_label="sub-sill section",
            representation="solid",
            generator=GeneratorRef(id="box", version=GEN),
            params=[_p("x0", -140, A), _p("x1", 120, A),
                    _p("y0", -42.5, A, note="derived: nut top"),
                    _p("h", 60, A, note="bounding box; exact extrusion not "
                                        "dimensioned")],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.36, 0.28, 0.66, 0.45), color="#e3c878",
            install_after=["nut", "cfc_packers"],
            note="Sub-sill section levelled on packers, secured by rod."),
        Component(
            id="sealant", type="sealant_bead",
            source_label="Sealant shown green",
            representation="solid",
            generator=GeneratorRef(id="bead", version=GEN),
            params=[_p("x0", -146, A), _p("width", 6, A,
                                          note="bead size not dimensioned "
                                               "in D4"),
                    _p("y0", -42.5, A), _p("y1", -12.5, A)],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.36, 0.29, 0.40, 0.33), color="#3fa34d",
            install_after=["subsill", "angle"],
            note="Colour per drawing; size assumed."),
        Component(
            id="fillet", type="fillet", source_label="Fillet",
            representation="solid",
            generator=GeneratorRef(id="fillet", version=GEN),
            params=[_p("x", 1.5, A), _p("y", -78.5, A,
                                        note="derived: membrane top"),
                    _p("leg_x", 15, A), _p("leg_y", 15, A)],
            product=ProductRef(note="unresolved: no system selected"),
            source_regions=_rd(0.33, 0.52, 0.37, 0.56), color="#2b2b2b",
            install_after=["membrane"]),
        Component(
            id="weep", type="weep_hole",
            source_label="Weep holes through sections",
            representation="annotation",
            generator=GeneratorRef(id="annotation", version=GEN),
            params=[_p("x", 100, A, note="indicative"),
                    _p("y", -20, A, note="indicative")],
            source_regions=_rd(0.60, 0.32, 0.66, 0.36), color="#555555",
            install_after=["subsill"],
            note="Openings not modelled in v0."),
    ]
    relations = [
        Relation(source="subsill", target="cfc_packers",
                 kind="supported_by"),
        Relation(source="subsill", target="nut", kind="supported_by"),
        Relation(source="nut", target="washer", kind="bonded_to"),
        Relation(source="washer", target="cfc_packers", kind="bonded_to"),
        Relation(source="cfc_packers", target="membrane", kind="bonded_to"),
        Relation(source="membrane", target="slab_upper", kind="bonded_to"),
        Relation(source="membrane", target="slab_lower", kind="bonded_to"),
        Relation(source="membrane", target="angle", kind="covers"),
        Relation(source="booker_rod", target="grout", kind="bonded_to"),
        Relation(source="grout", target="slab_lower", kind="bonded_to"),
        Relation(source="fillet", target="membrane", kind="bonded_to"),
        Relation(source="nut_patch", target="nut", kind="laps_over"),
        Relation(source="sealant", target="subsill", kind="sealed_to"),
        Relation(source="sealant", target="angle", kind="sealed_to"),
        Relation(source="cfc_packers", target="subsill",
                 kind="installed_before"),
        Relation(source="membrane", target="cfc_packers",
                 kind="installed_before"),
        Relation(source="booker_rod", target="grout",
                 kind="installed_before"),
        Relation(source="nut", target="subsill", kind="installed_before"),
    ]
    unresolved = [
        Unresolved(id="U-membrane-termination",
                   description="Membrane run continues past the crop edge.",
                   impacts=["membrane"]),
        Unresolved(id="U-subsill-profile",
                   description="Exact sub-sill extrusion not dimensioned; "
                               "bounding box.",
                   impacts=["subsill"]),
        Unresolved(id="U-rod-dims",
                   description="Rod diameter, embedment, spacing and stick-up "
                               "not dimensioned.",
                   impacts=["booker_rod", "grout", "washer", "nut"]),
        Unresolved(id="U-packer-buildup",
                   description="CFC packer count/thicknesses not dimensioned.",
                   impacts=["cfc_packers"]),
        Unresolved(id="U-extrusion",
                   description="Single section cannot fix 3D depth; 600mm "
                               "illustrative extent excluded from accuracy.",
                   impacts=["build"]),
        Unresolved(id="U-products",
                   description="No manufacturer system selected; every "
                               "product ref null.",
                   impacts=[c.id for c in comps if c.product is not None]),
        Unresolved(id="U-openings",
                   description="Weep holes annotation-only in v0.",
                   impacts=["weep"]),
    ]
    return DetailSpec(
        detail_id="conpro-subsill-D4",
        title="Sub-sill fixing in slab set down (D4)",
        origin="human-transcription-v1 (28 Oct 2026, embedded figure "
               "p0_Image22; documented seed, NOT model output)",
        source=SourceDoc(
            filename=CONPRO_FILENAME, sha256=CONPRO_SHA256, page_index=0,
            crop_id=CROPD, page_width_pt=540, page_height_pt=780),
        context={
            "location": "sub-sill section fixed in 80mm slab set-down",
            "situation": ["Residential", "External", "Balcony / Doorway", "Sub-sill fixing"],
            "substrate": "concrete slab (assumed; material not labelled)",
            "system": None,
            "illustrative_extent_z_mm": 600,
            "illustrative_extent_provenance": "assumption",
            "contact_checks": [
                ["cfc_packers", "membrane", "packers bed on membrane"],
                ["washer", "cfc_packers", "washer beds on packers"],
                ["nut", "washer", "nut beds on washer"],
                ["subsill", "nut", "sub-sill beds on nut"],
                ["fillet", "membrane", "fillet bonds to membrane"],
            ],
            "note": "Same reference sheet as D3; a different detail for a "
                    "different condition — not a product approval.",
        },
        components=comps, relations=relations, unresolved=unresolved,
    )


# The hand-transcribed reference details. They are served and corrected from
# code; only AI-made details live in the database.
SEEDS = {"conpro-door-sill-D3": door_sill_d3,
         "conpro-subsill-D4": door_subsill_d4}
SEED_IDS = frozenset(SEEDS)
