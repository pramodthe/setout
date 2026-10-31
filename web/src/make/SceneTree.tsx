import type { JSX } from "react";
import type { TreeNode } from "./edits";

function Node({ node, depth, selected, onSelect }: {
  node: TreeNode;
  depth: number;
  selected: string | null;
  onSelect: (name: string) => void;
}): JSX.Element {
  if (node.type === "mesh") {
    return (
      <button
        type="button"
        className="make-part"
        data-part={node.name}
        aria-selected={selected === node.name}
        style={{ paddingLeft: 8 + depth * 12 }}
        onClick={() => onSelect(node.name)}
      >
        {node.name}
      </button>
    );
  }
  return (
    <div>
      <div className="make-collection" style={{ paddingLeft: depth * 12 }}>{node.name}</div>
      {(node.children || []).map((child) => (
        <Node key={child.name} node={child} depth={depth + 1} selected={selected} onSelect={onSelect} />
      ))}
    </div>
  );
}

export default function SceneTree({ tree, selected, onSelect }: {
  tree: TreeNode | null;
  selected: string | null;
  onSelect: (name: string) => void;
}): JSX.Element {
  return (
    <div className="make-tree" data-make="tree">
      <h2>Scene</h2>
      {tree ? <Node node={tree} depth={0} selected={selected} onSelect={onSelect} /> : <p className="make-muted">No scene yet.</p>}
    </div>
  );
}
