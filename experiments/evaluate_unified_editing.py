import json
import shutil
from itertools import islice
from time import time
from typing import Tuple, Union
import sys
import os

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

#sys.path.append('/path/to/unified-model-editing')
from baselines.ft import FTHyperParams, apply_ft_to_model
from baselines.mend import MENDHyperParams, MendRewriteExecutor
from dsets import (
    AttributeSnippets,
    CounterFactDataset,
    MENDQADataset,
    MultiCounterFactDataset,
    get_tfidf_vectorizer,
)
from experiments.py.eval_utils_counterfact import compute_rewrite_quality_counterfact
from experiments.py.eval_utils_zsre import compute_rewrite_quality_zsre
from rome import ROMEHyperParams, apply_rome_to_model
from unified_editing import UNIFIEDHyperParams, apply_unified_to_model
from util import nethook
from util.globals import *

from glue_eval.glue_eval import GLUEEval

DS_DICT = {
    "mcf": (MultiCounterFactDataset, compute_rewrite_quality_counterfact),
    "cf": (CounterFactDataset, compute_rewrite_quality_counterfact),
    "zsre": (MENDQADataset, compute_rewrite_quality_zsre),
}


def main(
    alg_name: str,
    model_name: Union[str, Tuple],
    hparams_fname: str,
    ds_name: str,
    dataset_size_limit: int,
    continue_from_run: str,
    skip_generation_tests: bool,
    generation_test_interval: int,
    conserve_memory: bool,
    sequential: bool,
    downstream_eval_steps: int,
    dir_name: str,
    num_edits: int = 1,
    use_cache: bool = False,
):
    if alg_name == 'ROME':
        assert num_edits == 1, 'Unified editing with ROME is only applicable for singular edits. Batched edits with ROME are not allowed.'
    if alg_name == 'EMMET':
        assert num_edits > 1, 'EMMET resorts to the exact formulation of ROME for num_edits = 1. When using EMMET with num_edits=1, use ROME instead.'

    # Set algorithm-specific variables
    params_class, apply_algo = UNIFIEDHyperParams, apply_unified_to_model

    # Determine run directory
    # Create new dir if not continuing from prev run OR prev run doesn't exist
    if (
        continue_from_run is None
        or not (run_dir := RESULTS_DIR / dir_name / continue_from_run).exists()
    ):
        continue_from_run = None
    if continue_from_run is None:
        alg_dir = RESULTS_DIR / dir_name
        if alg_dir.exists():
            id_list = [
                int(str(x).split("_")[-1])
                for x in alg_dir.iterdir()
                if str(x).split("_")[-1].isnumeric()
            ]
            run_id = 0 if not id_list else max(id_list) + 1
        else:
            run_id = 0
        run_dir = RESULTS_DIR / dir_name / f"run_{str(run_id).zfill(3)}"
        run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Results will be stored at {run_dir}")

    # Get run hyperparameters
    params_path = (
        run_dir / "params.json"
        if continue_from_run is not None
        else HPARAMS_DIR / "UNIFIED" / hparams_fname
    )

    try:
        hparams = params_class.from_json(params_path)
    except:
        params_path = HPARAMS_DIR / "UNIFIED" / hparams_fname
        hparams = params_class.from_json(params_path)

    if not (run_dir / "params.json").exists():
        #shutil.copyfile(params_path, run_dir / "params.json")
        ####ADDING HPARAMETERS TO SAVE
        hparams_to_save = hparams.__dict__
        hparams_to_save['model_name'] = model_name
        hparams_to_save['algo_name'] = alg_name
        hparams_to_save['dataset'] = ds_name
        hparams_to_save['n_edits'] = num_edits
        hparams_to_save['sequential'] = sequential
        
        with open(run_dir / "params.json", "w") as f:
            json.dump(hparams_to_save, f, indent=1)

            
    print(f"Executing {alg_name} with parameters {hparams}")

    # Instantiate vanilla model
    if type(model_name) is str:
        print("Instantiating model")
        model = AutoModelForCausalLM.from_pretrained(model_name).cuda()
        original_model = AutoModelForCausalLM.from_pretrained(model_name)
        tok = AutoTokenizer.from_pretrained(model_name)
        tok.pad_token = tok.eos_token

        #store initial weights of the model
        original_weights = extract_model_original_weights(original_model, hparams)
        del original_model


    # Load data
    print("Loading dataset, attribute snippets, tf-idf data")
    snips = AttributeSnippets(DATA_DIR) if not skip_generation_tests else None
    vec = get_tfidf_vectorizer(DATA_DIR) if not skip_generation_tests else None

    #if num_edits > 1:
    #    assert ds_name != "cf", f"{ds_name} does not support multiple edits"

    ds_class, ds_eval_method = DS_DICT[ds_name]
    ds = ds_class(DATA_DIR, tok=tok, size=dataset_size_limit)

    # Get cache templates
    cache_template = None
    if use_cache:
        cache_template = (
            KV_DIR
            / f"{model_name.replace('/', '_')}_{alg_name}"
            / f"{ds_name}_layer_{{}}_clamp_{{}}_case_{{}}.npz"
        )
        print(f"Will load cache from {cache_template}")


    # Iterate through dataset
    glue_save_location = str(run_dir) + '/' + 'glue_eval/'
    os.makedirs(glue_save_location, exist_ok=True)


    # Iterate through dataset
    for r, record_chunks in enumerate(chunks(ds, num_edits)):
        case_result_template = str(run_dir / "{}_edits-case_{}.json")

        # Is the chunk already done?
        already_finished = True
        for record in record_chunks:
            if not Path(
                case_result_template.format(num_edits, record["case_id"])
            ).exists():
                already_finished = False
                break
        if already_finished:
            continue

        # Compute weight changes + record weights that changed
        case_ids = [record["case_id"] for record in record_chunks]
        args_conserve_memory = (
            dict(return_orig_weights_device=("cpu" if conserve_memory else "cuda"))
            if conserve_memory
            else dict()
        )
        etc_args = dict(cache_template=cache_template) if any(alg in alg_name for alg in ["ROME", "MEMIT"]) else dict()


        if r == 0:#do initial GLUE EVAL WITH ORIGINAL MODEL
            glue_results = {'edit_num': -1}

            out_file = glue_save_location + "base.json"
            if num_edits > 1:
                glue_eval = GLUEEval(model, tok)
                glue_results = glue_eval.evaluate(glue_results, out_file, sst_flag = True, mrpc_flag = True, cola_flag=True, rte_flag=True)

            #store the individual overall result file
            output_filename = out_file.replace('.json', '_glue.json')
            with open(output_filename, "w") as f:
                json.dump(glue_results, f, indent=4)


        start = time()
        edited_model, weights_copy, objective_distances = apply_algo(
            alg_name,
            model,
            tok,
            [
                {"case_id": record["case_id"], **record["requested_rewrite"]}
                for record in record_chunks
            ],
            hparams,
            copy=False,
            return_orig_weights=True,
            **args_conserve_memory,
            **etc_args,
        )

        exec_time = time() - start
        print("Execution took", exec_time)

        # Evaluate new model
        start = time()
        gen_test_vars = [snips, vec]
        for record in record_chunks:
            out_file = Path(case_result_template.format(num_edits, record["case_id"]))
            if out_file.exists():
                print(f"Skipping {out_file}; already exists")
                continue

            metrics = {
                "case_id": record["case_id"],
                "grouped_case_ids": case_ids,
                "num_edits": num_edits,
                "requested_rewrite": record["requested_rewrite"],
                "time": exec_time,
                "post": ds_eval_method(
                    edited_model,
                    tok,
                    record,
                    *(
                        gen_test_vars
                        if record["case_id"] % generation_test_interval == 0
                        else [None, None]
                    ),  # Only test generation every generation_test_interval cases
                ),
            }

            # Dump metrics in .json
            with open(out_file, "w") as f:
                json.dump(metrics, f, indent=1)



        if (r + 1) % downstream_eval_steps == 0:
            #Do GLUE EVALUATION
            distance = get_model_distance(original_weights, edited_model, hparams)

            glue_results = {
                'edit_num': r,
                'case_id': case_ids,
                'distance_from_original': distance,
                'objective_distances': objective_distances,
                }

            out_file = glue_save_location + "case_{}.json".format(record["case_id"])#stores the last case ID of the batch
            if num_edits > 1:
                glue_eval = GLUEEval(model, tok)
                glue_results = glue_eval.evaluate(glue_results, out_file, sst_flag = True, mrpc_flag = True, cola_flag=True, rte_flag=True)
            
            #store the individual overall result file
            output_filename = out_file.replace('.json', '_glue.json')
            with open(output_filename, "w") as f:
                json.dump(glue_results, f, indent=4)


        if not sequential:
            # Restore original weights
            with torch.no_grad():
                for k, v in weights_copy.items():
                    nethook.get_parameter(model, k)[...] = v.to("cuda")

        print("Evaluation took", time() - start)
        
def extract_model_original_weights(model, hparams):
    weights = {
        f"{hparams.rewrite_module_tmp.format(layer)}.weight": nethook.get_parameter(
            model, f"{hparams.rewrite_module_tmp.format(layer)}.weight"
        )
        for layer in hparams.layers
    }

    # Save old weights for future restoration. 
    weights_copy = {k: v.detach().clone() for k, v in weights.items()}

    return weights_copy



def get_model_distance(original_weights, model_new, model_hpar):
    state_dict_original = original_weights
    state_dict_new = model_new.state_dict()

    distances_dict = {}
    for layer in model_hpar.layers:
        if isinstance(layer, str) and 'transformer' in layer:
            rewrite_layer = layer
        else:
            rewrite_layer = model_hpar.rewrite_module_tmp.format(str(layer)) + '.weight'

        distance = torch.norm(state_dict_original[rewrite_layer] - state_dict_new[rewrite_layer].cpu()) / state_dict_original[rewrite_layer].numel()
        distances_dict[layer] = distance.detach().cpu().item()
    
    return distances_dict


def window(seq, n=2):
    "Returns a sliding window (of width n) over data from the iterable"
    "   s -> (s0,s1,...s[n-1]), (s1,s2,...,sn), ...                   "
    it = iter(seq)
    result = tuple(islice(it, n))
    if len(result) == n:
        yield result
    for elem in it:
        result = result[1:] + (elem,)
        yield result


def chunks(arr, n):
    """Yield successive n-sized chunks from arr."""
    for i in range(0, len(arr), n):
        yield arr[i : i + n]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--alg_name",
        choices=["MEMIT", "ROME", "EMMET"],
        default="ROME",
        help="Editing algorithm to use. Results are saved in results/<alg_name>/<run_id>, "
        "where a new run_id is generated on each run. "
        "If continuing from previous run, specify the run_id in --continue_from_run.",
        required=False,
    )
    parser.add_argument(
        "--model_name",
        choices=["gpt2-medium", "gpt2-large", "gpt2-xl", "EleutherAI/gpt-j-6B"],
        default="gpt2-xl",
        help="Model to edit.",
        required=False,
    )
    parser.add_argument(
        "--hparams_fname",
        type=str,
        default="gpt2-xl.json",
        help="Name of hyperparameters file, located in the hparams/<alg_name> folder.",
        required=False,
    )
    parser.add_argument(
        "--ds_name",
        choices=["mcf", "cf", "zsre"],
        default="mcf",
        help="Dataset to perform evaluations on. Either CounterFact (cf), MultiCounterFact (mcf), or zsRE (zsre).",
    )
    parser.add_argument(
        "--continue_from_run",
        type=str,
        default=None,
        help="If continuing from previous run, set to run_id. Otherwise, leave as None.",
    )
    parser.add_argument(
        "--dataset_size_limit",
        type=int,
        default=None,
        help="Truncate CounterFact to first n records.",
    )
    parser.add_argument(
        "--skip_generation_tests",
        dest="skip_generation_tests",
        action="store_true",
        help="Only run fast probability-based tests without slow generation tests. "
        "Useful for quick debugging and hyperparameter sweeps.",
    )
    parser.add_argument(
        "--generation_test_interval",
        type=int,
        default=1,
        help="One generation test is performed every [flag_value] iterations. If -1, generation tests are skipped.",
    )
    parser.add_argument(
        "--conserve_memory",
        dest="conserve_memory",
        action="store_true",
        help="Reduce memory usage during evaluation at the cost of a minor slowdown. "
        "Backs up model weights on CPU instead of GPU.",
    )
    parser.add_argument(
        "--num_edits",
        type=int,
        default=1,
        help="Number of rewrites to perform simultaneously.",
    )
    parser.add_argument(
        "--use_cache",
        dest="use_cache",
        action="store_true",
        help="Use cached k/v pairs",
    )
    parser.add_argument(
        "--sequential",
        type=bool,
        default=False,
        help="If we want to do sequential editing or not",
    )
    parser.add_argument(
        "--downstream_eval_steps",
        type=int,
        default=1,
        help="If we want to do sequential editing or not",
    )

    parser.set_defaults(skip_generation_tests=False, conserve_memory=False)
    args = parser.parse_args()

    main(
        args.alg_name,
        args.model_name,
        args.hparams_fname,
        args.ds_name,
        args.dataset_size_limit,
        args.continue_from_run,
        args.skip_generation_tests,
        args.generation_test_interval,
        args.conserve_memory,
        args.sequential,
        args.downstream_eval_steps,
        dir_name=args.alg_name,
        num_edits=args.num_edits,
        use_cache=args.use_cache,
    )
