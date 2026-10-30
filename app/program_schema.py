"""Strict wire schema for complex programs without tool-JSON escaping."""
import copy
from . import ontology
from .native_program import NativeProgram


def object_schema(properties):
    return {"type":"object", "properties":properties, "required":list(properties), "additionalProperties":False}


def program_schema(labels=None, provenance=("drawing", "assumption"), part_provenance=("drawing", "inferred")):
    """The authoring wire schema. labels: the READ's verbatim labels; a part's
    label is one of them or null, so the model cannot reword one. provenance:
    what this author may claim (an agent never creates user or manufacturer
    evidence; existing values of those kinds stay available in revisions).
    part_provenance: likewise for parts; an agent claims drawing or inferred
    (gated in native_program.prepare), never a reviewer's confirmation.
    class_id, assembly_class and inferred_by are a choice from the ontology or
    null: inferred_by offers every required slot, so nobody guesses its format."""
    schema = NativeProgram.model_json_schema()
    known = ontology.load()
    definitions = schema.pop("$defs")
    for field in ("source", "origin", "version", "detail_id"):
        schema["properties"].pop(field, None)
    def expand(value):
        if isinstance(value, list):
            return [expand(v) for v in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return expand(definitions[value["$ref"].rsplit("/",1)[-1]])
        result = {k:expand(v) for k,v in value.items() if k != "default" and not (k == "title" and isinstance(v, str))}
        if result.get("type") == "object" and "properties" in result:
            result.update(required=list(result["properties"]), additionalProperties=False)
        return result
    schema = expand(schema)
    string = {"type":"string"}
    nullable_string = {"type":["string","null"]}
    pair = object_schema({"name":string,"expr":string})
    part_schema = schema["properties"]["parts"]["items"]
    part = part_schema["properties"]
    part.pop("fixture", None)
    part_schema["required"].remove("fixture")
    if labels is not None:
        choices = sorted({label for label in labels if label})
        part["label"] = {"anyOf": [{"type": "string", "enum": choices}, {"type": "null"}]} if choices else {"type": "null"}
    schema["properties"]["params"]["items"]["properties"]["provenance"] = {"type": "string", "enum": list(provenance)}
    schema["properties"]["assembly_class"] = {"anyOf": [{"type": "string", "enum": known.assembly_ids()}, {"type": "null"}]}
    part["class_id"] = {"anyOf": [{"type": "string", "enum": known.component_ids()}, {"type": "null"}]}
    part["provenance"] = {"type": "string", "enum": list(part_provenance)}
    part["inferred_by"] = {"anyOf": [{"type": "string", "enum": [f"archetype:{assembly}@{known.version}:{slot.id}"
                                                                  for assembly in known.assembly_ids()
                                                                  for slot in known.required_slots(assembly)]},
                                     {"type": "null"}]}
    part["param_bindings"] = {"type":"array", "items":pair}
    part["placement"] = object_schema({axis:string for axis in ("x","y","z")})
    part["features"]["items"]["properties"]["selector"] = object_schema({
        "axis":nullable_string, "face":nullable_string, "other_part":nullable_string,
        "other_face":nullable_string, "center":{"type":["array","null"], "items":string}})
    schema["properties"]["relations"]["items"]["properties"]["constraints"] = object_schema({
        **{k:nullable_string for k in ("axis","source_face","target_face")},
        **{k:{"type":["number","null"]} for k in ("min_gap","max_gap","min_coverage")}})
    return schema


def from_wire(raw):
    raw = copy.deepcopy(raw)
    for part in raw.get("parts", []):
        bindings = part.get("param_bindings", [])
        if isinstance(bindings, list):
            if len({p["name"] for p in bindings}) != len(bindings):
                raise ValueError("duplicate CAD parameter bindings")
            part["param_bindings"] = {p["name"]:p["expr"] for p in bindings}
        for feature in part.get("features", []):
            feature["selector"] = {k:v for k,v in feature["selector"].items() if v is not None}
    for relation in raw.get("relations", []):
        relation["constraints"] = {k:v for k,v in relation["constraints"].items() if v is not None}
    return raw
