#!/bin/bash

nohup python experiments/evaluate_unified_editing.py --model_name EleutherAI/gpt-j-6B --num_edits 1024 --hparams_fname EleutherAI_gpt-j-6B.json --alg_name MEMIT  --ds_name mcf --mom2_weight_update 20000 > EleutherAI_gpt-j-6B_R-4_num_edits-1024_MEMIT_Mom2WeightUpdate-20000_Mom2NSample-100000_dSet-mcf.log 2>&1 &
p7_pid=$!
wait $p7_pid
echo "p7 has been executed."

nohup python experiments/evaluate_unified_editing.py --model_name EleutherAI/gpt-j-6B --num_edits 1024 --hparams_fname EleutherAI_gpt-j-6B.json --alg_name MEMIT  --ds_name mcf --mom2_weight_update 100000 > EleutherAI_gpt-j-6B_R-4_num_edits-1024_MEMIT_Mom2WeightUpdate-100000_Mom2NSample-100000_dSet-mcf.log 2>&1 &
p8_pid=$!
wait $p8_pid
echo "p8 has been executed."

