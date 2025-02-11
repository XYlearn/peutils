from binaryninja import log_info
import binaryninja.interaction
from binaryninja.flowgraph import FlowGraph, FlowGraphNode
from binaryninja.function import DisassemblyTextLine, InstructionTextToken
from binaryninja.enums import InstructionTextTokenType, BranchType

from .data import files as pe_files
from .pe_parsing import *

def generate_report(bv):
    imports = get_imports(bv)
    exports = get_exports(bv)

    out = "# Exports (0x%08x)\n" % get_eat_addr(bv)
    for export in exports:
        out += "- %r\n" % export

    out += "# Imports (0x%08x)\n" % get_iat_addr(bv)
    for import_ in imports:
        out += "- %r\n" % import_

    binaryninja.interaction.show_markdown_report(
        "%s (PE Tables)" % get_eat_name(bv), out
    )


well_known_libraries = [
    "dbghelp.dll", "d3dx9_41.dll", "d3d9.dll", "gdi32.dll", "dsound.dll",
    "ole32.dll", "shlwapi.dll", "dinput8.dll", "user32.dll", "imm32.dll",
    "iphlpapi.dll", "winmm.dll", "ws2_32.dll", "kernel32.dll", "setupapi.dll",
]
def generate_relation_graph(bvs):
    nodes = set()
    node_labels = dict()
    edges = dict()

    first_node = None
    bv_nodes = set()

    for bv in bvs:
        name = get_eat_name(bv)
        lower_name = name.lower()
        nodes.add(lower_name)
        bv_nodes.add(lower_name)
        node_labels[lower_name] = name

        if not first_node:
            first_node = lower_name

        edges[lower_name] = set()

        for library in get_imports(bv):
            imp_lower_name = library.name.lower()
            nodes.add(imp_lower_name)
            node_labels[imp_lower_name] = library.name

            edges[lower_name].add(imp_lower_name)

    graph_nodes = {}
    graph = FlowGraph()

    # If we have multiple binary views loaded that doesn't have direct
    # relationships then we'll need to group them under a start node as binja
    # currently only supports one start node for the flowgraphs
    # Figure out whether we need to do this or not
    start_node_count = 0
    start_graphnode = None
    start_nodes = []
    for start_node in edges:
        has_relation = False
        for related_node in [edge for edge in edges if edge != start_node]:
            log_info("Testing %s for %s" % (start_node, related_node))
            if start_node in edges[related_node]:
                has_relation = True
                log_info("Found relation")
                break

        if not has_relation:
            start_node_count += 1
            start_nodes.append(start_node)

            if start_node_count > 1:

                break

    start_graphnode = FlowGraphNode(graph)
    start_graphnode.lines = ["Start"]
    graph.append(start_graphnode)

    for node in nodes:
        graph_node = FlowGraphNode(graph)
        graph_node.lines = [str(node_labels[node])]
        graph.append(graph_node)

        if node.lower() in start_nodes and node in bv_nodes:
            log_info("Startnode: %s" % node)
            start_graphnode.add_outgoing_edge(BranchType.UnconditionalBranch, graph_node)

        graph_nodes[node.lower()] = graph_node

    for node, graph_node in graph_nodes.items():
        if node.lower() not in edges:
            continue

        for edge in edges[node.lower()]:
            log_info("%s -> %s" % (node, edge))
            if node.lower() in bv_nodes and edge.lower() in bv_nodes:
                graph_node.add_outgoing_edge(BranchType.TrueBranch, graph_nodes[edge.lower()])
            elif edge.lower() in well_known_libraries:
                graph_node.add_outgoing_edge(BranchType.UnconditionalBranch, graph_nodes[edge.lower()])
            else:
                graph_node.add_outgoing_edge(BranchType.FalseBranch, graph_nodes[edge.lower()])

    binaryninja.interaction.show_graph_report("Relation graph", graph)


def generate_table_graph(bv):
    graph = FlowGraph()

    binary = FlowGraphNode(graph)
    binary.lines = ["Binary"]
    graph.append(binary)

    export_table = FlowGraphNode(graph)
    lines = [
        [
            InstructionTextToken(
                InstructionTextTokenType.TextToken, "Exports"
            ),
            InstructionTextToken(
                InstructionTextTokenType.OperandSeparatorToken, ":"
            ),
        ],
        [],
    ]

    for export in get_exports(bv):
        lines.append([
            InstructionTextToken(
                InstructionTextTokenType.AddressDisplayToken,
                "%04x" % export.ord,
                value=export.ord,
            ),
            InstructionTextToken(
                InstructionTextTokenType.OperandSeparatorToken, "    "
            ),
            InstructionTextToken(
                InstructionTextTokenType.CodeRelativeAddressToken,
                "0x%08x" % export.addr,
                value=export.addr
            ),
            InstructionTextToken(
                InstructionTextTokenType.OperandSeparatorToken, " @ "
            ),
            InstructionTextToken(
                InstructionTextTokenType.CodeSymbolToken, "%s" % export.name,
                value=export.addr
            ),
        ])

    export_table.lines = [DisassemblyTextLine(tokens) for tokens in lines]

    import_head = FlowGraphNode(graph)
    import_head.lines = ["Imports"]

    graph.append(export_table)
    graph.append(import_head)
    binary.add_outgoing_edge(BranchType.UnconditionalBranch, export_table)
    binary.add_outgoing_edge(BranchType.UnconditionalBranch, import_head)

    for lib in get_imports(bv):
        lines = []
        node = FlowGraphNode(graph)
        lines = [
            [
                InstructionTextToken(
                    InstructionTextTokenType.TextToken,
                    lib.name,
                ),
                InstructionTextToken(
                    InstructionTextTokenType.OperandSeparatorToken,
                    " (",
                ),
                InstructionTextToken(
                    InstructionTextTokenType.RegisterToken
                    if lib.name.lower() in pe_files
                    else InstructionTextTokenType.CharacterConstantToken,

                    "Loaded" if lib.name.lower() in pe_files
                    else "Not loaded",
                ),
                InstructionTextToken(
                    InstructionTextTokenType.OperandSeparatorToken,
                    ")",
                ),
            ],
            [],
        ]

        exports = []
        if lib.name.lower() in pe_files:
            exports = get_exports(pe_files[lib.name.lower()])

        n = 0
        for import_ in lib.imports:
            name = import_.name if import_.name else "Ordinal %x" % import_.ordinal

            for export in exports:
                if export.ord != import_.ordinal:
                    continue

                name = export.name

            lines.append([
                InstructionTextToken(
                    InstructionTextTokenType.AddressDisplayToken, "%04x" % n,
                    value=n,
                ),
                InstructionTextToken(
                    InstructionTextTokenType.OperandSeparatorToken, "    "
                ),
                InstructionTextToken(
                    InstructionTextTokenType.CodeRelativeAddressToken,
                    "0x%08x" % import_.datavar_addr,
                    value=import_.datavar_addr,
                ),
                InstructionTextToken(
                    InstructionTextTokenType.OperandSeparatorToken, " @ "
                ),
                InstructionTextToken(
                    InstructionTextTokenType.ImportToken, "%s" % name,
                    value=import_.datavar_addr,
                ),
            ])
            n += 1

        node.lines = [DisassemblyTextLine(tokens) for tokens in lines]
        graph.append(node)
        import_head.add_outgoing_edge(BranchType.UnconditionalBranch, node)

    binaryninja.interaction.show_graph_report("%s (PE Table Graph)" % get_eat_name(bv), graph)
