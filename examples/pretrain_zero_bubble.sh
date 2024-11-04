#!/bin/bash


#SBATCH <SLURM OPTIONS> --nodes=128 --exclusive --ntasks-per-node=8 --job-name=megatron_gpt3_175b

export CUDA_DEVICE_MAX_CONNECTIONS=1
export CUDA_VISIBLE_DEVICES=6,7

DIR=`pwd`
DATETIME=`date +'date_%y-%m-%d_time_%H-%M-%S'`
mkdir -p $DIR/logs

DATASET="/tmp/dataset_gemma/dataset/c4_text_document"

if [ ! -e "$DATASET"".idx" ]; then
  tar -xvf dataset_gemma.tar -C /tmp
fi

# Running locally
if [ -z "$WORLD_SIZE" ]; then
  export WORLD_SIZE=1
  export RANK=0
  export MASTER_ADDR=localhost
  export MASTER_PORT=10086
fi

if [ -z "$GPUS_PER_NODE" ]; then
  GPUS_PER_NODE=$(nvidia-smi --list-gpus | wc -l)
  # GPUS_PER_NODE=1
fi

GPUS_PER_NODE=2

if [ -z "$EXIT_INTERVAL" ]; then
  EXIT_INTERVAL=1000
fi

WORLD_SIZE_IN_GPUS=$(( $WORLD_SIZE * $GPUS_PER_NODE ))

if [ -z "$PIPELINE_SIZE" ]; then
  PIPELINE_SIZE=2
  LAYERS=8
  MICRO_BATCH_SIZE=1
  GLOBAL_BATCH_SIZE=8
  HIDDEN_SIZE=4096
  ATTENTION_HEADS=32
  ZERO_BUBBLE_MEM_LIMIT=$((2 * $PIPELINE_SIZE))
fi

profile_ranks="0"
for ((i = 1; i < $WORLD_SIZE_IN_GPUS; i++)); do
    profile_ranks="$profile_ranks $i"
done
if [ -z "$ZERO_BUBBLE_TIMER_START" ]; then
  ZERO_BUBBLE_TIMER_START=100
  ZERO_BUBBLE_TIMER_END=110
fi

if [ -z "$EVAL_INTERVAL" ]; then
  EVAL_INTERVAL=10000
fi

if [ -z "$TP_SIZE" ]; then
  TP_SIZE=1
fi

options=" \
  --tensor-model-parallel-size $TP_SIZE \
  --pipeline-model-parallel-size $PIPELINE_SIZE \
  --num-layers $LAYERS \
  --hidden-size $HIDDEN_SIZE \
  --num-attention-heads $ATTENTION_HEADS \
  --exit-interval $EXIT_INTERVAL \
  --seq-length 4096 \
  --max-position-embeddings 8192 \
  --micro-batch-size $MICRO_BATCH_SIZE \
  --global-batch-size $GLOBAL_BATCH_SIZE \
  --train-samples 146484375 \
  --lr-decay-samples 126953125 \
  --lr-warmup-samples 183105 \
  --lr 6.0e-5 \
  --min-lr 6.0e-6 \
  --lr-decay-style cosine \
  --log-interval 10 \
  --eval-iters 40 \
  --eval-interval $EVAL_INTERVAL \
  --data-path ${DATASET} \
  --tokenizer-type GPTSentencePieceTokenizer \
  --tokenizer-model /tmp/dataset_gemma/tokenizers/tokenizer.model \
  --split 98,2,0 \
  --clip-grad 8.0 \
  --weight-decay 0.1 \
  --adam-beta1 0.9 \
  --adam-beta2 0.95 \
  --init-method-std 0.006 \
  --no-barrier-with-level-1-timing \
  --profile-step-start 150 \
  --profile-step-end 170 \
  --untie-embeddings-and-output-weights \
  --use-legacy-models \
  --sequence-parallel \
  --use-flash-attn \
  --transformer-impl local \
  --use-distributed-optimizer \
  --no-create-attention-mask-in-dataloader \
  --calculate-per-token-loss \
  --no-async-tensor-model-parallel-allreduce \
  --profile-ranks $profile_ranks \
  --attention-dropout 0 \
  --hidden-dropout 0 \
  --use-cpu-initialization \
  --no-gradient-accumulation-fusion"
  # --zero-bubble-v-schedule \
  # --zero-bubble-v-schedule-mem-setup half \
  # --enable-optimizer-post-validation \

if [ ! -z "$VOCAB_PARALLEL" ]; then
  options="$options --enable-vocab-parallel"
fi

if [ -z "$FP32" ]; then
  options="$options --fp16"
fi

if [ ! -z "$PROFILED" ]; then
  options="$options --profile"
fi

if [ ! -z "$ZERO_BUBBLE_V_SCHEDULE" ]; then
  ENABLE_ZERO_BUBBLE=1
  options="$options --zero-bubble-v-schedule "
fi

if [ ! -z "$ENABLE_ZERO_BUBBLE" ]; then
  options="$options --enable-zero-bubble \
  --zero-bubble-pipeline-timers-start-iter $ZERO_BUBBLE_TIMER_START \
  --zero-bubble-pipeline-timers-end-iter $ZERO_BUBBLE_TIMER_END \
  --zero-bubble-max-pending-backward $ZERO_BUBBLE_MEM_LIMIT"
  if [ -z "$FP32" ]; then
    options="$options --enable-optimizer-post-validation"
  fi
fi

if [ ! -z "$ENABLE_EXACTLY_NUMERIC_MATCH" ]; then
  options="$options --enable-exactly-numeric-match \
  --attention-dropout 0.0 \
  --hidden-dropout 0.0"
fi

if [ ! -z "$INTERLEAVED_1F1B" ]; then
  options="$options --num-layers-per-virtual-pipeline-stage 1"
fi

run_cmd="torchrun --nnodes $WORLD_SIZE \
  --node_rank $RANK \
  --master_addr $MASTER_ADDR \
  --master_port $MASTER_PORT \
  --nproc_per_node=$GPUS_PER_NODE ${DIR}/pretrain_gpt.py $@ ${options}"

if [ ! -z "$PROFILED" ]; then
  run_cmd="nsys profile -s none -t nvtx,cuda \
    --output $AIP_RUN_NAME.$RANK.nsys-rep \
    --force-overwrite true \
    --capture-range=cudaProfilerApi \
    --capture-range-end=stop \
    $run_cmd"
fi

echo $run_cmd
# sleep 100000
eval $run_cmd

set +x
