from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class FuncType(Enum):
    """
    For each microbatch, the order of the passes is as follows:
    IF -> REDUCE_INPUT_EMBD_FORWARD --> F --> BROADCAST_OUTPUT_EMBD_S -->
    S --> REDUCE_OUTPUT_EMBD_S --> (BROADCAST_OUTPUT_EMBD_T --> T --> REDUCE_OUTPUT_EMBD_T)
    --> B --> BROADCAST_INPUT_EMBD_BACKWARD --> IB

    Without disable_backward_fusion, there are no dependencies between T and B.
    """
    F = "F"
    B = "B"
    W = "W"
    BW = "BW"
    S = "S" # Output embedding
    T = "T" # Output embedding
    IF = "IF" # Input embedding forward
    IB = "IB" # Input embedding backward
    SEND_FORWARD = "SEND_FORWARD"
    RECV_FORWARD = "RECV_FORWARD"
    SEND_BACKWARD = "SEND_BACKWARD"
    RECV_BACKWARD = "RECV_BACKWARD"
    REDUCE_INPUT_EMBD_FORWARD = "REDUCE_INPUT_EMBD_FORWARD"
    BROADCAST_INPUT_EMBD_BACKWARD = "BROADCAST_INPUT_EMBD_BACKWARD"
    BROADCAST_OUTPUT_EMBD_S = "BROADCAST_OUTPUT_EMBD_S"
    BROADCAST_OUTPUT_EMBD_T = "BROADCAST_OUTPUT_EMBD_T"
    REDUCE_OUTPUT_EMBD_S = "REDUCE_OUTPUT_EMBD_S"
    REDUCE_OUTPUT_EMBD_T = "REDUCE_OUTPUT_EMBD_T"
    POST_VALIDATION = "POST_VALIDATION"
    SEND_POST_VALIDATION = "SEND_POST_VALIDATION"
    RECV_POST_VALIDATION = "RECV_POST_VALIDATION"

    def __str__(self):
        return self.value

    def __repr__(self):
        return self.value

    def is_computation(self):
        return self in {F, B, W, BW, S, T, IF, IB}

    def is_send(self):
        return self in {
            FuncType.SEND_FORWARD,
            FuncType.SEND_BACKWARD,
            FuncType.SEND_POST_VALIDATION,
        }

    def is_recv(self):
        return self in {
            FuncType.RECV_FORWARD,
            FuncType.RECV_BACKWARD,
            FuncType.RECV_POST_VALIDATION,
            # Broadcast are classified as recv for reordering purposes
            FuncType.BROADCAST_INPUT_EMBD_BACKWARD,
            FuncType.BROADCAST_OUTPUT_EMBD_S,
            FuncType.BROADCAST_OUTPUT_EMBD_T,
        }

    def peer_type(self):
        pairs = [
            (FuncType.SEND_FORWARD, FuncType.RECV_FORWARD),
            (FuncType.SEND_BACKWARD, FuncType.RECV_BACKWARD),
            (FuncType.SEND_POST_VALIDATION, FuncType.RECV_POST_VALIDATION),
        ]
        m = {k: v for k, v in pairs}
        m.update({v: k for k, v in pairs})
        return m[self]

    def is_backward_comm(self):
        return self in {
            FuncType.SEND_BACKWARD,
            FuncType.RECV_BACKWARD,
        }

    def is_post_validation_related(self):
        return self in (
            FuncType.POST_VALIDATION,
            FuncType.SEND_POST_VALIDATION,
            FuncType.RECV_POST_VALIDATION,
        )


F = FuncType.F
B = FuncType.B
W = FuncType.W
BW = FuncType.BW
S = FuncType.S
T = FuncType.T
IF = FuncType.IF
IB = FuncType.IB


class CommDirection(Enum):
    NEXT = 0
    PREV = 1


@dataclass(eq=True, frozen=True)
class NodeKey:
    type: FuncType
    layer_group_idx: int
    microbatch: int
    seq_split_idx: int

    def __post_init__(self):
        assert isinstance(self.type, FuncType)

    def __hash__(self):
        return hash((self.type, self.layer_group_idx, self.microbatch, self.seq_split_idx))


@dataclass(eq=True)
class ScheduledNode:
    type: FuncType
    stage: int
    microbatch: int
    chunk: int = 0
    seq_split_idx: int = 0
    # None for post validation ops
    layer_group_idx: Optional[int] = None
    start_time: Optional[int] = None
    completion_time: Optional[int] = None
    # Only for computation node
    # None means peer is on the same stage.
    recv_peer_stage: Optional[int] = None
    send_peer_stage: Optional[int] = None
    # Only for communication node
    comm_direction: Optional[CommDirection] = None
    comm_peer_stage: Optional[int] = None
    comm_pair_id: Optional[int] = None
    rollback: bool = False

    def __post_init__(self):
        assert isinstance(self.type, FuncType)

    def __hash__(self):
        return hash(self.get_key())

    def get_key(self):
        return NodeKey(self.type, self.layer_group_idx, self.microbatch, self.seq_split_idx)

    def get_prev_key(self, n_layer_groups: int):
        assert self.layer_group_idx is not None
        if self.type == F:
            if self.layer_group_idx == 0:
                return None
            prev_layer_group_idx = self.layer_group_idx - 1
            return NodeKey(self.type, prev_layer_group_idx, self.microbatch, self.seq_split_idx)
        if self.type in (B, BW):
            prev_layer_group_idx = self.layer_group_idx + 1
            assert prev_layer_group_idx <= n_layer_groups
            if prev_layer_group_idx == n_layer_groups:
                return NodeKey(F, self.layer_group_idx, self.microbatch, self.seq_split_idx)
            return NodeKey(self.type, prev_layer_group_idx, self.microbatch, self.seq_split_idx)
        assert self.type == W
        return NodeKey(B, self.layer_group_idx, self.microbatch, self.seq_split_idx)


@dataclass
class GraphConfig:
    mem_f: List[float] = None
    mem_b: List[float] = None
    mem_w: List[float] = None
    max_mem: Optional[List[float]] = None
    cost_f: List[float] = None
    cost_b: List[float] = None
    cost_w: List[float] = None
    cost_comm: float = 0.0
    print_scaling: int = 1
    max_chunks: int = 1
    n_stages: int = None
    n_micro: int = None

    def num_layer_groups(self):
        return self.n_stages * self.max_chunks

    @classmethod
    def basic_config(self, f, b, w, n_stages, n_micro, max_chunks):
        return GraphConfig(
            mem_f=[],
            mem_b=[],
            mem_w=[],
            cost_f=[f] * n_stages,
            cost_b=[b] * n_stages,
            cost_w=[w] * n_stages,
            max_chunks=max_chunks,
            n_stages=n_stages,
            n_micro=n_micro,
        )

    def __post_init__(self):
        assert all([isinstance(cost_f, float) for cost_f in self.cost_f])
        assert all([isinstance(cost_b, float) for cost_b in self.cost_b])
        assert all([isinstance(cost_w, float) for cost_w in self.cost_w])
        assert isinstance(self.cost_comm, float)
        assert all([f + b + w == 0 for (f, b, w) in zip(self.mem_f, self.mem_b, self.mem_w)])
        assert self.n_stages is not None
        assert self.n_micro is not None

    def get_cost(self, stage: int, func_type: FuncType):
        if func_type == BW:
            return self.cost_b[stage] + self.cost_w[stage]
        return {
            F: self.cost_f,
            B: self.cost_b,
            W: self.cost_w,
        }[func_type][stage]
