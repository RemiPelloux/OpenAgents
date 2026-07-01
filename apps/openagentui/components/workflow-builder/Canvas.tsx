"use client";

import {
  Background,
  Controls,
  ReactFlow,
  applyEdgeChanges,
  applyNodeChanges,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type OnConnect,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { NODE_RENDERER_TYPES, type WorkflowNodeData } from "./nodes/WorkflowNodeView";

type WFNode = Node<WorkflowNodeData>;

interface CanvasProps {
  nodes: WFNode[];
  edges: Edge[];
  onNodesChange: (nodes: WFNode[]) => void;
  onEdgesChange: (edges: Edge[]) => void;
  onConnect: OnConnect;
  onNodeClick: (nodeId: string | null) => void;
}

export function Canvas({ nodes, edges, onNodesChange, onEdgesChange, onConnect, onNodeClick }: CanvasProps) {
  return (
    <div className="oaui-canvas-wrap" style={{ height: "100%" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_RENDERER_TYPES}
        onNodesChange={(changes) => {
          // Applied by the parent via applyNodeChanges to keep a single
          // source of truth (the Workflow object), not duplicated state.
          onNodesChange(applyChangesLocally(nodes, changes));
        }}
        onEdgesChange={(changes) => {
          onEdgesChange(applyEdgeChangesLocally(edges, changes));
        }}
        onConnect={onConnect}
        onNodeClick={(_, node) => onNodeClick(node.id)}
        onPaneClick={() => onNodeClick(null)}
        fitView
        colorMode="dark"
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}

function applyChangesLocally(nodes: WFNode[], changes: NodeChange<WFNode>[]): WFNode[] {
  return applyNodeChanges<WFNode>(changes, nodes);
}

function applyEdgeChangesLocally(edges: Edge[], changes: EdgeChange[]): Edge[] {
  return applyEdgeChanges(changes, edges);
}
