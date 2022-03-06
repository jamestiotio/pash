import argparse
import sys
import pickle
import traceback
from datetime import datetime
from typing import List, Set, Tuple, Dict
sys.path.append("/pash/compiler")

import config
from ir import *
from ast_to_ir import compile_asts
from json_ast import *
from ir_to_ast import to_shell
from util import *

from definitions.ir.aggregator_node import *

from definitions.ir.nodes.eager import *
from definitions.ir.nodes.pash_split import *

import definitions.ir.nodes.r_merge as r_merge
import definitions.ir.nodes.r_split as r_split
import definitions.ir.nodes.r_unwrap as r_unwrap
import definitions.ir.nodes.dgsh_tee as dgsh_tee
import definitions.ir.nodes.remote_exec as remote_exec
import definitions.ir.nodes.remote_pipe as remote_pipe
import shlex
import subprocess
import pash_runtime


HOST = '0.0.0.0'

def get_available_port():
    # There is a possible race condition using the returned port as it could be opened by a different process
    port = config.next_available_port
    config.next_available_port += 1
    return port

def read_graph(filename):
    with open(filename, "rb") as ir_file:
        ir, shell_vars = pickle.load(ir_file)
    return ir, shell_vars
            
def graph_to_shell(graph):
    _, filename = ptempfile()
    
    dirs = set()
    for edge in graph.all_fids():
        directory = os.path.join(config.PASH_TMP_PREFIX, edge.prefix)
        dirs.add(directory)
    for directory in dirs:
        os.makedirs(directory, exist_ok=True)

    if not config.pash_args.no_eager:
        graph = pash_runtime.add_eager_nodes(graph, config.pash_args.dgsh_tee)

    script = to_shell(graph, config.pash_args)
    with open(filename, "w") as f:
        f.write(script)
    return filename

def add_remote_pipes(graphs, file_id_gen):
    for idx, level in enumerate(graphs):
        for sub_graph in level:
            sink_nodes = sub_graph.sink_nodes()
            assert(len(sink_nodes) == 1)

            for edge in sub_graph.get_node_output_fids(sink_nodes[0]):
                stdin = file_id_gen.next_file_id()
                stdin.set_resource(FileDescriptorResource(('fd', 0)))
                stdout = file_id_gen.next_file_id()
                stdout.set_resource(FileDescriptorResource(('fd', 1)))
                sub_graph.add_edge(stdout)
                write_port = get_available_port()
                if not edge.is_ephemeral():
                    if edge.has_file_resource() or not edge.get_resource().is_stdout():
                        raise NotImplementedError
                    stdout.set_resource(edge.get_resource())
                    edge.make_ephemeral()
                
                remote_write = remote_pipe.make_remote_pipe([edge.get_ident()], [stdout.get_ident()], HOST, write_port, False, False)
                sub_graph.add_node(remote_write)

                if idx < len(graphs) - 1:
                    matching_subgraph = None
                    for sg in graphs[idx + 1]:
                        if edge in sg.all_fids():
                            matching_subgraph = sg
                            break
                    new_edge = file_id_gen.next_ephemeral_file_id()
                    matching_subgraph.replace_edge(edge.get_ident(), new_edge)
                    remote_read = remote_pipe.make_remote_pipe([], [new_edge.get_ident()], HOST, write_port, True, True)
                    matching_subgraph.add_node(remote_read)

        return graphs, write_port

def split_ir(graph: IR):
    file_id_gen = graph.get_file_id_gen()
    source_node_ids = graph.source_nodes()
    input_fifo_map = {}
    graphs = []
    level = [(source, IR({}, {})) for source in source_node_ids]
    next_level = []
    
    while level:
        for old_node_id, sub_graph in level:
            node = graph.get_node(old_node_id).copy()
            node_id = node.get_id()
            
            for idx, input_fid in enumerate(graph.get_node_input_fids(old_node_id)):
                input_edge_id = None
                # If subgraph is empty and edge isn't ephemeral the edge needs to be added
                if not input_fid.get_ident() in sub_graph.edges:
                    new_fid = input_fid
                    sub_graph.add_to_edge(new_fid, node_id)
                    input_edge_id = new_fid.get_ident()
                else:
                    input_edge_id = input_fid.get_ident()
                    sub_graph.set_edge_to(input_edge_id, node_id)
                # keep track  
                input_fifo_map[input_edge_id] = sub_graph
            # Add edges coming out of the node
            for output_fid in graph.get_node_output_fids(old_node_id):
                sub_graph.add_from_edge(node_id, output_fid)
            
            # Add the node
            sub_graph.add_node(node)

            next_ids = graph.get_next_nodes(old_node_id)
            if len(next_ids) > 1: #branching
                graphs.append([sub_graph])
                for next_id in next_ids:
                    next_level.append((next_id, IR({}, {})))
            elif len(next_ids) == 0: # last node
                graphs.append([sub_graph])
            else:
                next_level.append((next_ids[0], sub_graph))

        next_unique_nodes = set([nid for nid, _ in next_level])
        if len(next_unique_nodes) < len(next_level): # merging
            graphs.append([sub_graph for _, sub_graph in next_level])
            next_level = []
            for node_id in next_unique_nodes:
                next_level.append((node_id, IR({}, {})))

        level = next_level
        next_level = []

    return graphs, file_id_gen, input_fifo_map

def add_remote_pipes(graphs:List[List[IR]], file_id_gen: FileIdGen, mapping:Dict):
    write_port = -1
    # The graph to execute in the main pash_runtime
    final_subgraph = IR({}, {})
    for idx, level in enumerate(graphs):
        for sub_graph in level:
            sink_nodes = sub_graph.sink_nodes()
            source_nodes = sub_graph.source_nodes()
            assert(len(sink_nodes) == 1)
            # assert(len(source_nodes) == 1)
            
            # Transform output edges
            for out_edge in sub_graph.get_node_output_fids(sink_nodes[0]):
                stdout = file_id_gen.next_file_id()
                stdout.set_resource(FileDescriptorResource(('fd', 1)))
                sub_graph.add_edge(stdout)
                write_port = get_available_port()
                out_edge_id = out_edge.get_ident()
                # Replace the old edge with an ephemeral edge in case it isn't and
                # to avoid modifying the edge in case it's used in some other subgraph
                ephemeral_edge = file_id_gen.next_ephemeral_file_id()
                sub_graph.replace_edge(out_edge_id, ephemeral_edge)

                # Add remote-write node at the end of the subgraph
                remote_write = remote_pipe.make_remote_pipe([ephemeral_edge.get_ident()], [stdout.get_ident()], HOST, write_port, False, False)
                sub_graph.add_node(remote_write)
                
                # Copy the old output edge resource
                new_edge = file_id_gen.next_file_id()
                new_edge.set_resource(out_edge.get_resource())

                if out_edge_id in mapping and out_edge.is_ephemeral():
                    matching_subgraph = mapping[out_edge_id]
                    matching_subgraph.replace_edge(out_edge.get_ident(), new_edge)
                else:
                    matching_subgraph = final_subgraph
                    matching_subgraph.add_edge(new_edge)
                
                remote_read = remote_pipe.make_remote_pipe([], [new_edge.get_ident()], HOST, write_port, True, True)
                matching_subgraph.add_node(remote_read)

    return final_subgraph

def prepare_graph_for_remote_exec(filename):
    """
    Reads the complete ir from filename and splits it
    into subgraphs where ony the first subgraph represent a continues
    segment (merger segment or branched segment) in the graph. 
    Note: All subgraphs(except first one) read and write from remote pipes.
        However, we had to add a fake stdout to avoid some problems when converting to shell code.

    Returns: 
        subgraphs: List of subgraphs
        shell_vars: shell variables
        final_graph_fname: filename of the script to execute on the master machine. 
            This script will contain edges to correctly redict the original sink and source nodes

    
    TODO: change overall design to decouple all the subgraphs from the 
    first stdin and last stdout. This will allow us to run the first segment
    remotly instead of locally. This is only useful if first segment is longer 
    than just a split could be worth it for some benchmarks.
    """
    ir, shell_vars = read_graph(filename)
    graphs, file_id_gen, mapping = split_ir(ir)
    final_subgraph = add_remote_pipes(graphs, file_id_gen, mapping) 
    ret = []

    # Flattening the graph
    for level in graphs:
        for sub_graph in level:
            ret.append(sub_graph)
    
    final_graph_fname = graph_to_shell(final_subgraph)

    return ret, shell_vars, final_graph_fname