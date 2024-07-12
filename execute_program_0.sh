#!/bin/bash

nohup python experiments/evaluate_unified_editing.py --model_name EleutherAI/gpt-j-6B --num_edits 1024 --hparams_fname EleutherAI_gpt-j-6B.json --alg_name MEMIT  --ds_name mcf --mom2_weight_update 1 > EleutherAI_gpt-j-6B_R-4_num_edits-1024_MEMIT_Mom2WeightUpdate-1_Mom2NSample-100000_dSet-mcf.log 2>&1 &
p1_pid=$!
wait $p1_pid
echo "p1 has been executed."

nohup python experiments/evaluate_unified_editing.py --model_name EleutherAI/gpt-j-6B --num_edits 1024 --hparams_fname EleutherAI_gpt-j-6B.json --alg_name MEMIT  --ds_name mcf --mom2_weight_update 50 > EleutherAI_gpt-j-6B_R-4_num_edits-1024_MEMIT_Mom2WeightUpdate-50_Mom2NSample-100000_dSet-mcf.log 2>&1 &
p2_pid=$!
wait $p2_pid
echo "p2 has been executed."

echo "all have been executed."