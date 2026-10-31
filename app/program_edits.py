"""Bounded JSON-pointer edits to an immutable program proposal."""
import copy

ROOTS = {"params", "parts", "relations", "unresolved", "title", "kind", "situation"}


def label(item):
    return (item.get("id") or item.get("name")) if isinstance(item, dict) else None


def list_index(items,key,append=False):
    if key == "-" and append:
        return len(items)
    if key.startswith("@"):
        matches = [i for i,item in enumerate(items) if label(item) == key[1:]]
        if len(matches) != 1:
            raise ValueError("edit reference is missing or ambiguous: "+key)
        return matches[0]
    return int(key)


def apply_edits(program, edits):
    result = copy.deepcopy(program)
    if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
        raise ValueError("provide between 1 and 100 edits")
    for edit in edits:
        op, path = edit.get("op"), edit.get("path")
        if op not in {"add", "replace", "remove"} or not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("edits require add/replace/remove and an absolute JSON pointer")
        keys = [key.replace("~1", "/").replace("~0", "~") for key in path[1:].split("/")]
        if keys[0] not in ROOTS:
            raise ValueError("cannot edit server identity or source fields")
        target = result
        for key in keys[:-1]:
            if isinstance(target,list):
                index = list_index(target,key)
                if index < 0 or index >= len(target):
                    raise ValueError("edit path array index out of range")
                target = target[index]
            else:
                target = target[key]
        key = keys[-1]
        if isinstance(target, list):
            index = list_index(target,key,op == "add")
            if index < 0 or index > len(target) or (op != "add" and index == len(target)):
                raise ValueError("array edit index out of range")
            if op == "add":
                # A second definition of a name could not then be reached by that name.
                same = [item for item in target if label(edit["value"]) and label(item) == label(edit["value"])]
                if same and same[0] != edit["value"]:
                    parent = "/" + "/".join(keys[:-1])
                    raise ValueError(f"{parent} already has {label(edit['value'])}: replace {parent}/@{label(edit['value'])}")
                if not same:
                    target.insert(index, copy.deepcopy(edit["value"]))
            elif op == "remove":
                target.pop(index)
            else:
                target[index] = copy.deepcopy(edit["value"])
        elif isinstance(target, dict):
            if op != "add" and key not in target:
                raise ValueError("edit path does not exist")
            if op == "remove":
                del target[key]
            else:
                target[key] = copy.deepcopy(edit["value"])
        else:
            raise ValueError("edit parent must be an object or array")
    return result
