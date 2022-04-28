from definitions.ir.dfg_node import *
# from definitions.ir.nodes.arg import Arg
from util_new_cmd_invocations import get_command_invocation_prefix_from_dfg_node


## This class corresponds to a generic n-ary aggregator
##
## TODO: Do we need to do anything special for binary aggregators?
class MapperAggregatorNode(DFGNode):
    def __init__(self, old_node, input_ids, output_ids, name_string, new_options, flag_option_list):

        ## The name of the aggregator command
        name = Arg(string_to_argument(name_string))

        ## TODO: The category should also be acquired through annotations (and maybe should be asserted to be at most pure)
        com_category="pure"

        ## TODO: Not sure if redirections need to be copied to new function.
        com_redirs = [redir.to_ast() for redir in old_node.com_redirs]
        super().__init__(input_ids,
                         output_ids, 
                         name,
                         com_category,
                         # BEGIN ANNO
                         # OLD
                         # com_options=old_node.com_options,
                         # NEW
                         com_options=new_options, # changed that all are already in there and not appended
                         flag_option_list=flag_option_list,
                         # END ANNO
                         com_redirs=com_redirs, 
                         com_assignments=old_node.com_assignments)

        ## TODO: This assumes that all options from the old function are copied to the new.
        ##
        ## TODO: If we need a behavior where we don't keep the old flags, we can extend this
        # BEGIN ANNO
        # OLD
        # self.append_options(new_options)
        # END ANNO


class AggregatorNode(MapperAggregatorNode):
    def __init__(self, old_node, input_ids, output_ids):

        # BEGIN ANNO
        used_parallelizer = old_node.get_used_parallelizer()
        cmd_inv_pref = get_command_invocation_prefix_from_dfg_node(old_node)
        used_aggregator = used_parallelizer.get_actual_aggregator(cmd_inv_pref)
        log(f'used_agg: {used_aggregator}')
        log(f'old_node: {old_node}')
        # END ANNO

        ## Check if an aggregator can be instantiated from the node
        # BEGIN ANNO
        # OLD
        # if(old_node.com_aggregator is None):
        # NEW
        if(used_aggregator is None):
        # END ANNO
            log("Error: Node:", old_node, "does not contain information to instantiate an aggregator!")
            raise Exception('No information to instantiate aggregator')

        ## The name of the aggregator command
        # BEGIN ANNO
        # OLD
        # agg_name_string = old_node.com_aggregator.name
        # new_options = old_node.com_aggregator.options
        # NEW
        agg_name_string = used_aggregator.cmd_name
        all_options_incl_new = [Arg.string_to_arg(el.get_name()) for el in used_aggregator.flag_option_list + used_aggregator.positional_config_list]
        # TODO: zip is nicer
        all_options_incl_new_right_format = [(i, all_options_incl_new[i]) for i in range(len(all_options_incl_new))]
        # END ANNO

        # BEGIN ANNO
        # OLD
        # super().__init__(old_node, input_ids, output_ids, agg_name_string, new_options)
        # NEW
        super().__init__(old_node, input_ids, output_ids, agg_name_string, all_options_incl_new_right_format,
                         flag_option_list=used_aggregator.flag_option_list)
        # END ANNO

        log("Generic Aggregator Created:", self)

class MapperNode(MapperAggregatorNode):
    def __init__(self, old_node, input_ids, output_ids):

        assert(False)
        ## Check if an mapper can be instantiated from the node
        if(old_node.com_mapper is None):
            log("Error: Node:", old_node, "does not contain information to instantiate a mapper!")
            raise Exception('No information to instantiate mapper')

        ## The name of the aggregator command
        mapper_name_string = old_node.com_mapper.name
        new_options = old_node.com_mapper.options

        super().__init__(old_node, input_ids, output_ids, mapper_name_string, new_options)

        log("Generic Mapper Created:", self)
