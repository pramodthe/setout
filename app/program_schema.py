"""Strict wire schema for complex programs without tool-JSON escaping."""
import copy
from .native_program import NativeProgram


def object_schema(properties):
    return {"type":"object", "properties":properties, "required":list(properties), "additionalProperties":False}


def program_schema():
    schema = NativeProgram.model_json_schema()
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
