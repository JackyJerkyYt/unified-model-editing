#!/bin/bash
nohup python experiments/evaluate_unified_editing.py --model_name EleutherAI/gpt-j-6B --num_edits 1024 --hparams_fname EleutherAI_gpt-j-6B.json --alg_name MEMIT  --ds_name mcf --mom2_weight_update 3000 > EleutherAI_gpt-j-6B_R-4_num_edits-1024_MEMIT_Mom2WeightUpdate-3000_Mom2NSample-100000_dSet-mcf.log 2>&1 &
p5_pid=$!
wait $p5_pid
echo "p5 has been executed."

nohup python experiments/evaluate_unified_editing.py --model_name EleutherAI/gpt-j-6B --num_edits 1024 --hparams_fname EleutherAI_gpt-j-6B.json --alg_name MEMIT  --ds_name mcf --mom2_weight_update 4000 > EleutherAI_gpt-j-6B_R-4_num_edits-1024_MEMIT_Mom2WeightUpdate-4000_Mom2NSample-100000_dSet-mcf.log 2>&1 &
p6_pid=$!
wait $p6_pid
echo "p6 has been executed."