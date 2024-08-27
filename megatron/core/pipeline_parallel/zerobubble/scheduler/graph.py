from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class FuncType(Enum):
    F = "F"
    B = "B"
    W = "W"
    BW = "BW"
    SEND_FORWARD = "SEND_FORWARD"
    RECV_FORWARD = "RECV_FORWARD"
    SEND_BACKWARD = "SEND_BACKWARD"
    RECV_BACKWARD = "RECV_BACKWARD"
    POST_VALIDATION = "POST_VALIDATION"
    SEND_POST_VALIDATION = "SEND_POST_VALIDATION"
    RECV_POST_VALIDATION = "RECV_POST_VALIDATION"

    def is_computation(self):
        return self in {F, B, W, BW}

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
        }

    def is_backward(self):
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


class CommDirection(Enum):
    NEXT = 0
    PREV = 1


@dataclass(eq=True, frozen=True)
class NodeKey:
    type: FuncType
    stage: int
    minibatch: int
    chunk: int = 0

    def __post_init__(self):
        assert isinstance(self.type, FuncType)

    def __hash__(self):
        return hash((self.type, self.stage, self.minibatch, self.chunk))


@dataclass(eq=True, frozen=True)
class ScheduledNode:
    type: FuncType
    stage: int
    microbatch: int
    chunk: int = 0
    start_time: Optional[int] = None
    completion_time: Optional[int] = None
    prev_compute_node: Optional[NodeKey] = None
    # Only for computation node
    recv_peer_stage: Optional[int] = None
    send_peer_stage: Optional[int] = None
    # Only for communication node
    comm_direction: Optional[CommDirection] = None
    rollback: bool = False

    def __post_init__(self):
        assert isinstance(self.type, FuncType)

    def get_key(self):
        return NodeKey(self.type, self.stage, self.microbatch, self.chunk)


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
