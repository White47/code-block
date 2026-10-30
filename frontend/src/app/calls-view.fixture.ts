import type { Graph } from '../types';

// Captured from DesktopBridge using owned AST-only run.py/helpers.py fixtures.
// Only frontend Graph fields are retained; original node/edge IDs, anchors and confidence are unchanged.

export const overviewCalls: Graph = {
  "viewKind": "Calls",
  "nodes": [
    {
      "id": "node_49fa2abc162687208270676d",
      "displayName": "helpers",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_49fa2abc162687208270676d"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 1,
          "startColumn": null,
          "endLine": 5,
          "endColumn": null
        }
      ],
      "memberIds": [],
      "codeName": "helpers",
      "role": "Module",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_1bb66d4dbc66cdddaadd3658",
      "displayName": "RuntimeError",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_1bb66d4dbc66cdddaadd3658"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 1,
          "startColumn": 6,
          "endLine": 1,
          "endColumn": 55
        }
      ],
      "memberIds": [],
      "codeName": "helpers.RuntimeError",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_82d9ae738ebe8ef72f668479",
      "displayName": "RuntimeError",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_82d9ae738ebe8ef72f668479"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 1,
          "startColumn": 6,
          "endLine": 1,
          "endColumn": 18
        }
      ],
      "memberIds": [],
      "codeName": "builtins.RuntimeError",
      "role": "ExternalSymbol",
      "confidence": "Probable",
      "metadata": {}
    },
    {
      "id": "node_3075e6ad0734b1a103c406d3",
      "displayName": "normalize",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_3075e6ad0734b1a103c406d3"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 2,
          "startColumn": 0,
          "endLine": 3,
          "endColumn": 23
        }
      ],
      "memberIds": [],
      "codeName": "helpers.normalize",
      "role": "Function",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_911d6df6c3add34c8f5f5e33",
      "displayName": "clean",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_911d6df6c3add34c8f5f5e33"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 3,
          "startColumn": 11,
          "endLine": 3,
          "endColumn": 23
        }
      ],
      "memberIds": [],
      "codeName": "helpers.normalize.clean",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_64cbecb00d898ec79b3a0e64",
      "displayName": "clean",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_64cbecb00d898ec79b3a0e64"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 4,
          "startColumn": 0,
          "endLine": 5,
          "endColumn": 24
        }
      ],
      "memberIds": [],
      "codeName": "helpers.clean",
      "role": "Function",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_82edd5a82a079297b1fc1cb1",
      "displayName": "strip",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_82edd5a82a079297b1fc1cb1"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 5,
          "startColumn": 11,
          "endLine": 5,
          "endColumn": 24
        }
      ],
      "memberIds": [],
      "codeName": "helpers.clean.strip",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_b8170452ea150d3b1de1be7a",
      "displayName": "strip",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_b8170452ea150d3b1de1be7a"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 5,
          "startColumn": 11,
          "endLine": 5,
          "endColumn": 24
        }
      ],
      "memberIds": [],
      "codeName": "helpers.clean.strip",
      "role": "UnresolvedSymbol",
      "confidence": "Unresolved",
      "metadata": {}
    },
    {
      "id": "node_d98d77ab976f89bd6f46ee9f",
      "displayName": "run",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_d98d77ab976f89bd6f46ee9f"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 1,
          "startColumn": null,
          "endLine": 6,
          "endColumn": null
        }
      ],
      "memberIds": [],
      "codeName": "run",
      "role": "Module",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_2daf99d0d005585367d2abe8",
      "displayName": "RuntimeError",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_2daf99d0d005585367d2abe8"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 1,
          "startColumn": 6,
          "endLine": 1,
          "endColumn": 55
        }
      ],
      "memberIds": [],
      "codeName": "run.RuntimeError",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_0d458b8402b5a7dd3aab525b",
      "displayName": "RuntimeError",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_0d458b8402b5a7dd3aab525b"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 1,
          "startColumn": 6,
          "endLine": 1,
          "endColumn": 18
        }
      ],
      "memberIds": [],
      "codeName": "builtins.RuntimeError",
      "role": "ExternalSymbol",
      "confidence": "Probable",
      "metadata": {}
    },
    {
      "id": "node_f95800a91895c9adb53a9606",
      "displayName": "main",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_f95800a91895c9adb53a9606"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 3,
          "startColumn": 0,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "memberIds": [],
      "codeName": "run.main",
      "role": "Function",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_cac96f729b37107c355e9398",
      "displayName": "normalize",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_cac96f729b37107c355e9398"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 4,
          "startColumn": 11,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "memberIds": [],
      "codeName": "run.main.normalize",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_efa22ca6ec2a4131f02c82ad",
      "displayName": "main",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_efa22ca6ec2a4131f02c82ad"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "memberIds": [],
      "codeName": "run.main",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    }
  ],
  "edges": [
    {
      "source": "node_1bb66d4dbc66cdddaadd3658",
      "target": "node_82d9ae738ebe8ef72f668479",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 1,
          "startColumn": 6,
          "endLine": 1,
          "endColumn": 55
        }
      ],
      "confidence": "Probable"
    },
    {
      "source": "node_911d6df6c3add34c8f5f5e33",
      "target": "node_64cbecb00d898ec79b3a0e64",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 3,
          "startColumn": 11,
          "endLine": 3,
          "endColumn": 23
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_82edd5a82a079297b1fc1cb1",
      "target": "node_b8170452ea150d3b1de1be7a",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 5,
          "startColumn": 11,
          "endLine": 5,
          "endColumn": 24
        }
      ],
      "confidence": "Unresolved"
    },
    {
      "source": "node_2daf99d0d005585367d2abe8",
      "target": "node_0d458b8402b5a7dd3aab525b",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 1,
          "startColumn": 6,
          "endLine": 1,
          "endColumn": 55
        }
      ],
      "confidence": "Probable"
    },
    {
      "source": "node_cac96f729b37107c355e9398",
      "target": "node_3075e6ad0734b1a103c406d3",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 4,
          "startColumn": 11,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_efa22ca6ec2a4131f02c82ad",
      "target": "node_f95800a91895c9adb53a9606",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_49fa2abc162687208270676d",
      "target": "node_1bb66d4dbc66cdddaadd3658",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 1,
          "startColumn": 0,
          "endLine": 1,
          "endColumn": 55
        },
        {
          "filePath": "helpers.py",
          "startLine": 1,
          "startColumn": 6,
          "endLine": 1,
          "endColumn": 55
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_3075e6ad0734b1a103c406d3",
      "target": "node_911d6df6c3add34c8f5f5e33",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 3,
          "startColumn": 4,
          "endLine": 3,
          "endColumn": 23
        },
        {
          "filePath": "helpers.py",
          "startLine": 3,
          "startColumn": 11,
          "endLine": 3,
          "endColumn": 23
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_64cbecb00d898ec79b3a0e64",
      "target": "node_82edd5a82a079297b1fc1cb1",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 5,
          "startColumn": 4,
          "endLine": 5,
          "endColumn": 24
        },
        {
          "filePath": "helpers.py",
          "startLine": 5,
          "startColumn": 11,
          "endLine": 5,
          "endColumn": 24
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_d98d77ab976f89bd6f46ee9f",
      "target": "node_2daf99d0d005585367d2abe8",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 1,
          "startColumn": 0,
          "endLine": 1,
          "endColumn": 55
        },
        {
          "filePath": "run.py",
          "startLine": 1,
          "startColumn": 6,
          "endLine": 1,
          "endColumn": 55
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_f95800a91895c9adb53a9606",
      "target": "node_cac96f729b37107c355e9398",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 4,
          "startColumn": 4,
          "endLine": 4,
          "endColumn": 27
        },
        {
          "filePath": "run.py",
          "startLine": 4,
          "startColumn": 11,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_d98d77ab976f89bd6f46ee9f",
      "target": "node_efa22ca6ec2a4131f02c82ad",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        },
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "confidence": "Exact"
    }
  ],
  "rootIds": [],
  "layoutKind": "directed",
  "layoutHints": {
    "node_49fa2abc162687208270676d": [
      -110.0,
      0.0
    ],
    "node_d98d77ab976f89bd6f46ee9f": [
      110.0,
      0.0
    ],
    "node_1bb66d4dbc66cdddaadd3658": [
      -220.0,
      112.0
    ],
    "node_2daf99d0d005585367d2abe8": [
      0.0,
      112.0
    ],
    "node_efa22ca6ec2a4131f02c82ad": [
      220.0,
      112.0
    ],
    "node_82d9ae738ebe8ef72f668479": [
      -220.0,
      224.0
    ],
    "node_0d458b8402b5a7dd3aab525b": [
      0.0,
      224.0
    ],
    "node_f95800a91895c9adb53a9606": [
      220.0,
      224.0
    ],
    "node_cac96f729b37107c355e9398": [
      0.0,
      336.0
    ],
    "node_3075e6ad0734b1a103c406d3": [
      0.0,
      448.0
    ],
    "node_911d6df6c3add34c8f5f5e33": [
      0.0,
      560.0
    ],
    "node_64cbecb00d898ec79b3a0e64": [
      0.0,
      672.0
    ],
    "node_82edd5a82a079297b1fc1cb1": [
      0.0,
      784.0
    ],
    "node_b8170452ea150d3b1de1be7a": [
      0.0,
      896.0
    ]
  },
  "legend": [
    {
      "type": "CALLS",
      "label": "Calls",
      "style": "calls-dashed"
    },
    {
      "type": "CONTAINS",
      "label": "Contains",
      "style": "structural-dotted"
    }
  ],
  "hiddenCounts": {
    "nodes": 0,
    "edges": 0
  },
  "queryContext": {
    "controlMode": "relations",
    "structureDepth": 1,
    "canExpand": false,
    "depthLimit": false,
    "hasMore": false,
    "budgetTruncated": false
  },
  "issues": []
};

export const focusedCalls: Graph = {
  "viewKind": "Calls",
  "nodes": [
    {
      "id": "node_f95800a91895c9adb53a9606",
      "displayName": "main",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_f95800a91895c9adb53a9606"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 3,
          "startColumn": 0,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "memberIds": [],
      "codeName": "run.main",
      "role": "Function",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_cac96f729b37107c355e9398",
      "displayName": "normalize",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_cac96f729b37107c355e9398"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 4,
          "startColumn": 11,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "memberIds": [],
      "codeName": "run.main.normalize",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_3075e6ad0734b1a103c406d3",
      "displayName": "normalize",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_3075e6ad0734b1a103c406d3"
      ],
      "sourceAnchors": [
        {
          "filePath": "helpers.py",
          "startLine": 2,
          "startColumn": 0,
          "endLine": 3,
          "endColumn": 23
        }
      ],
      "memberIds": [],
      "codeName": "helpers.normalize",
      "role": "Function",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_d98d77ab976f89bd6f46ee9f",
      "displayName": "run",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_d98d77ab976f89bd6f46ee9f"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 1,
          "startColumn": null,
          "endLine": 6,
          "endColumn": null
        }
      ],
      "memberIds": [],
      "codeName": "run",
      "role": "Module",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_efa22ca6ec2a4131f02c82ad",
      "displayName": "main",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_efa22ca6ec2a4131f02c82ad"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "memberIds": [],
      "codeName": "run.main",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    }
  ],
  "edges": [
    {
      "source": "node_cac96f729b37107c355e9398",
      "target": "node_3075e6ad0734b1a103c406d3",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 4,
          "startColumn": 11,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_efa22ca6ec2a4131f02c82ad",
      "target": "node_f95800a91895c9adb53a9606",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_f95800a91895c9adb53a9606",
      "target": "node_cac96f729b37107c355e9398",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 4,
          "startColumn": 4,
          "endLine": 4,
          "endColumn": 27
        },
        {
          "filePath": "run.py",
          "startLine": 4,
          "startColumn": 11,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_d98d77ab976f89bd6f46ee9f",
      "target": "node_efa22ca6ec2a4131f02c82ad",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        },
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "confidence": "Exact"
    }
  ],
  "rootIds": [
    "node_f95800a91895c9adb53a9606"
  ],
  "layoutKind": "directed",
  "layoutHints": {
    "node_d98d77ab976f89bd6f46ee9f": [
      0.0,
      0.0
    ],
    "node_efa22ca6ec2a4131f02c82ad": [
      0.0,
      112.0
    ],
    "node_f95800a91895c9adb53a9606": [
      0.0,
      224.0
    ],
    "node_cac96f729b37107c355e9398": [
      0.0,
      336.0
    ],
    "node_3075e6ad0734b1a103c406d3": [
      0.0,
      448.0
    ]
  },
  "legend": [
    {
      "type": "CALLS",
      "label": "Calls",
      "style": "calls-dashed"
    },
    {
      "type": "CONTAINS",
      "label": "Contains",
      "style": "structural-dotted"
    }
  ],
  "hiddenCounts": {
    "nodes": 0,
    "edges": 0
  },
  "queryContext": {
    "controlMode": "relations",
    "structureDepth": 1,
    "canExpand": false,
    "depthLimit": false,
    "hasMore": false,
    "budgetTruncated": false
  },
  "issues": []
};

export const upstreamCalls: Graph = {
  "viewKind": "Calls",
  "nodes": [
    {
      "id": "node_f95800a91895c9adb53a9606",
      "displayName": "main",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_f95800a91895c9adb53a9606"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 3,
          "startColumn": 0,
          "endLine": 4,
          "endColumn": 27
        }
      ],
      "memberIds": [],
      "codeName": "run.main",
      "role": "Function",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_d98d77ab976f89bd6f46ee9f",
      "displayName": "run",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_d98d77ab976f89bd6f46ee9f"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 1,
          "startColumn": null,
          "endLine": 6,
          "endColumn": null
        }
      ],
      "memberIds": [],
      "codeName": "run",
      "role": "Module",
      "confidence": "Exact",
      "metadata": {}
    },
    {
      "id": "node_efa22ca6ec2a4131f02c82ad",
      "displayName": "main",
      "semanticComponentIds": [],
      "codeNodeIds": [
        "node_efa22ca6ec2a4131f02c82ad"
      ],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "memberIds": [],
      "codeName": "run.main",
      "role": "CallSite",
      "confidence": "Exact",
      "metadata": {}
    }
  ],
  "edges": [
    {
      "source": "node_efa22ca6ec2a4131f02c82ad",
      "target": "node_f95800a91895c9adb53a9606",
      "type": "CALLS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "confidence": "Exact"
    },
    {
      "source": "node_d98d77ab976f89bd6f46ee9f",
      "target": "node_efa22ca6ec2a4131f02c82ad",
      "type": "CONTAINS",
      "codeEdgeIds": [],
      "semanticComponentIds": [],
      "sourceAnchors": [
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        },
        {
          "filePath": "run.py",
          "startLine": 6,
          "startColumn": 4,
          "endLine": 6,
          "endColumn": 18
        }
      ],
      "confidence": "Exact"
    }
  ],
  "rootIds": [
    "node_f95800a91895c9adb53a9606"
  ],
  "layoutKind": "directed",
  "layoutHints": {
    "node_d98d77ab976f89bd6f46ee9f": [
      0.0,
      0.0
    ],
    "node_efa22ca6ec2a4131f02c82ad": [
      0.0,
      112.0
    ],
    "node_f95800a91895c9adb53a9606": [
      0.0,
      224.0
    ]
  },
  "legend": [
    {
      "type": "CALLS",
      "label": "Calls",
      "style": "calls-dashed"
    },
    {
      "type": "CONTAINS",
      "label": "Contains",
      "style": "structural-dotted"
    }
  ],
  "hiddenCounts": {
    "nodes": 0,
    "edges": 0
  },
  "queryContext": {
    "controlMode": "relations",
    "structureDepth": 1,
    "canExpand": false,
    "depthLimit": false,
    "hasMore": false,
    "budgetTruncated": false
  },
  "issues": []
};
