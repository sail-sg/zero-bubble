from collections import deque

from megatron.core.pipeline_parallel.zerobubble.scheduler.graph import ScheduledNode, F, B, W, FuncType


class PipelineGraph(object):
    def __init__(
        self, n_stage, n_micro, f_cost, b_cost, w_cost, c_cost,
        f_mem, b_mem, w_mem, max_mem=None,
    ):
        self.n_node = 6 * n_stage * n_micro
        self.n_stage = n_stage
        self.n_micro = n_micro
        self.f_cost = f_cost
        self.b_cost = b_cost
        self.w_cost = w_cost
        self.c_cost = c_cost
        self.f_mem = f_mem
        self.b_mem = b_mem
        self.w_mem = w_mem
        self.fbw_cost = [f_cost, b_cost, w_cost]
        self.fbw_mem = [f_mem, b_mem, w_mem]
        self.max_mem = max_mem or f_mem * self.n_stage * 2

    def get_id(self, cat, chunk, stage, micro):
        return cat * 2 * self.n_stage * self.n_micro + \
               chunk * self.n_stage * self.n_micro + \
               stage * self.n_micro + \
               micro

    def try_v_schedule(self, fill_f=True, fill_b=True, approved_bubble=None):
        count = []
        for i in range(self.n_stage):
            count.append([0] * 6)

        end_time = [-1] * self.n_node
        cur_time = [0] * self.n_stage
        mem = [0] * self.n_stage
        stage_bubble = [0] * self.n_stage
        pending_w = [deque() for _ in range(self.n_stage)]
        schedule = [[] for _ in range(self.n_stage)]
        stage_str = ["    " * i for i in range(self.n_stage)]

        if approved_bubble is None:
            approved_bubble = [-1] * self.n_stage
        max_approved_bubble = max(approved_bubble)

        def get_max_stage_bubble(stage=-1):
            max_stage_bubble = 0
            for bb in stage_bubble:
                max_stage_bubble = max(max_stage_bubble, bb)
            if stage >= 0:
                max_stage_bubble = max(max_stage_bubble, max_approved_bubble - approved_bubble[stage])
            return max_stage_bubble

        def put_w(stage):
            assert len(pending_w[stage]) > 0
            _, chunk_, _ = pending_w[stage].popleft()
            put(2, chunk_, stage)

        def put(cat, chunk, stage, assert_cnt=True):
            _tmp = _no_bubble = cur_time[stage] + self.fbw_cost[cat]
            _cnt = count[stage][cat * 2 + chunk]
            # assert _cnt < self.n_micro
            if _cnt >= self.n_micro:
                if not assert_cnt:
                    stage_str[stage] += "    "
                    cur_time[stage] = _tmp  # TODO
                    return
                assert False
            assert mem[stage] + self.fbw_mem[cat] <= self.max_mem
            stage_str[stage] += "FfBbWw"[cat * 2 + chunk] + str(_cnt + 1) + " " * (3 - len(str(_cnt + 1)))
            if cat > 0 or chunk > 0:
                last_id = cat * 2 + chunk - 1
                if cat < 2:
                    # if end_time[self.get_id(last_id // 2, last_id % 2, stage, _cnt)] < 0:
                    #     print(cat, chunk, stage, _cnt)
                    #     self.print_details(end_time)
                    assert end_time[self.get_id(last_id // 2, last_id % 2, stage, _cnt)] >= 0
                else:
                    assert end_time[self.get_id(1, chunk, stage, _cnt)] >= 0
            if chunk == 1 and cat < 2:
                if stage < self.n_stage - 1:
                    _fa_id = self.get_id(cat, chunk, stage + 1, _cnt)
                    assert end_time[_fa_id] >= 0
                    _tmp = max(_tmp, end_time[_fa_id] + self.c_cost + self.fbw_cost[cat])
            if chunk == 0 and cat < 2:
                if stage > 0:
                    _fa_id = self.get_id(cat, chunk, stage - 1, _cnt)
                    # if end_time[_fa_id] < 0:
                    #     print(cat, chunk, stage, _cnt)
                    #     self.print_details(end_time)
                    assert end_time[_fa_id] >= 0, f"{cat}, {chunk}, {stage}, {_cnt}"
                    _tmp = max(_tmp, end_time[_fa_id] + self.c_cost + self.fbw_cost[cat])
            _id = self.get_id(cat, chunk, stage, _cnt)
            if count[stage][0] > 0:
                stage_bubble[stage] += _tmp - _no_bubble
            end_time[_id] = _tmp
            cur_time[stage] = _tmp
            mem[stage] += self.fbw_mem[cat]
            # noinspection PyTypeChecker
            schedule[stage].append((cat, chunk, _cnt))
            if cat == 1:
                pending_w[stage].append((2, chunk, _cnt))
            count[stage][cat * 2 + chunk] += 1

        # for _ in range(2 * self.n_stage):
        #     for i in range(self.n_stage):
        #         if count[i][1] >= count[i][0]:
        #             put(0, 0, i, assert_cnt=False)
        #             continue
        #         if i == self.n_stage - 1:
        #             put(0, 1, i, assert_cnt=False)
        #             continue
        #         fa_id = self.get_id(0, 1, i + 1, count[i][1])
        #         if 0 <= end_time[fa_id] < cur_time[i + 1]:  # TODO
        #             put(0, 1, i, assert_cnt=False)
        #         else:
        #             put(0, 0, i, assert_cnt=False)

        for i in range(self.n_stage):
            put(0, 0, i)
        for i in range(self.n_stage - 1, -1, -1):
            if i == self.n_stage - 1:
                put(0, 1, i)
                continue
            tmp = end_time[self.get_id(0, 1, i + 1, 0)] + self.c_cost
            while mem[i] + self.fbw_mem[0] * (2 + i * 2) <= self.max_mem and cur_time[i] + self.fbw_cost[0] <= tmp and count[i][0] < self.n_micro:
                for j in range(i + 1):
                    put(0, 0, j)
            put(0, 1, i)
        iter_chunk_ = 0
        end_tmp = 0
        for i in range(self.n_stage):
            if i == 0:
                end_tmp = cur_time[0] + self.fbw_cost[1]
                continue
            tmp = end_tmp + self.c_cost
            while count[i][0] + count[i][1] < count[i - 1][0] + count[i - 1][1] or count[i][1] <= count[i - 1][1] < self.n_micro:
                for j in range(self.n_stage - 1, i - 1, -1):
                    if count[j][iter_chunk_] < self.n_micro:
                        put(0, iter_chunk_, j)
                iter_chunk_ = 1 - iter_chunk_
            # while mem[i] + self.fbw_mem[0] <= self.max_mem and cur_time[i] + self.fbw_cost[0] <= tmp:
            #     if iter_chunk_ == 0 and count[i][0] >= count[i - 1][0]:
            #         break
            #     for j in range(self.n_stage - 1, i - 1, -1):
            #         if count[j][iter_chunk_] < self.n_micro:
            #             put(0, iter_chunk_, j)
            #     iter_chunk_ = 1 - iter_chunk_
            # end_tmp = max(tmp, cur_time[i]) + self.fbw_cost[1]

        # init_bubble = get_max_stage_bubble()
        # print(stage_bubble)
        for _ in range(2 * self.n_micro):
            # check mem before putting b
            for i in range(self.n_stage):
                while mem[i] + self.fbw_mem[1] > self.max_mem:
                    assert len(pending_w[i]) > 0
                    put_w(i)
            b0_ranks, b1_ranks = [], []
            for i in range(self.n_stage):
                if count[i][3] >= count[i][2]:
                    b0_ranks.append(i)
                elif i == self.n_stage - 1:
                    b1_ranks.append(i)
                else:
                    fa_id = self.get_id(1, 1, i + 1, count[i][3])
                    if end_time[fa_id] >= 0 or count[i][2] >= self.n_micro:
                        b1_ranks.append(i)
                    else:
                        b0_ranks.append(i)
            b_ranks = []
            # put b1
            for i in reversed(b1_ranks):
                b_ranks.append((i, 1))
            # put b0
            for i in b0_ranks:
                b_ranks.append((i, 0))
            for i, _chunk_ in b_ranks:
                fa_id = -1
                if _chunk_ == 1 and i < self.n_stage - 1:
                    fa_id = self.get_id(1, 1, i + 1, count[i][3])
                if _chunk_ == 0 and i > 0:
                    fa_id = self.get_id(1, 0, i - 1, count[i][2])
                while len(pending_w[i]) > 0 and fa_id >= 0 and end_time[fa_id] + self.c_cost >= cur_time[i] + self.fbw_cost[2]:
                    # fill the bubble
                    put_w(i)
                if len(pending_w[i]) > 0 and end_time[fa_id] + self.c_cost - cur_time[i] > get_max_stage_bubble(i) - stage_bubble[i]:
                    if _chunk_ == 1:
                        put_w(i)
                    elif fill_b:
                        put_w(i)
                put(1, _chunk_, i)

            # put f
            for i in range(self.n_stage):
                if count[i][1] >= self.n_micro:
                    continue
                put_item = None
                if count[i][1] >= count[i][0]:
                    put_item = 0
                elif i == self.n_stage - 1:
                    put_item = 1
                else:
                    if end_time[self.get_id(0, 1, i + 1, count[i][1])] >= 0:
                        put_item = 1
                    elif count[i][0] < self.n_micro:
                        if i == 0:
                            put_item = 0
                        elif end_time[self.get_id(0, 0, i - 1, count[i][0])] >= 0:
                            put_item = 0
                if put_item is None:
                    continue
                # check mem before putting f
                while mem[i] + self.fbw_mem[0] > self.max_mem:
                    assert len(pending_w[i]) > 0
                    put_w(i)
                fa_id = -1
                if put_item == 0 and i > 0:
                    fa_id = self.get_id(0, 0, i - 1, count[i][0])
                if put_item == 1 and i < self.n_stage - 1:
                    fa_id = self.get_id(0, 1, i + 1, count[i][1])
                while len(pending_w[i]) > 0 and fa_id >= 0 and end_time[fa_id] + self.c_cost >= cur_time[i] + self.fbw_cost[2]:
                    # fill the bubble
                    put_w(i)
                if len(pending_w[i]) > 0 and end_time[fa_id] + self.c_cost - cur_time[i] > get_max_stage_bubble(i) - stage_bubble[i]:
                    if fill_f:
                        put_w(i)
                put(0, put_item, i)

        for i in range(self.n_stage):
            while len(pending_w[i]) > 0:
                put_w(i)

        # for i in range(self.n_stage):
        #     print(stage_str[i])

        max_bubble = get_max_stage_bubble()
        expected_time = sum(self.fbw_cost) * self.n_micro * 2
        bubble_rate = max_bubble / expected_time
        # print("%6.4f" % bubble_rate, "->", stage_bubble)
        if max_approved_bubble < 0 or max_bubble < max_approved_bubble:
            _schedule, _end_time, _max_bubble = self.try_v_schedule(
                fill_f=fill_f, fill_b=fill_b,
                approved_bubble=stage_bubble,
            )
            if _max_bubble < max_bubble:
                return _schedule, _end_time, _max_bubble
        # print("%2d %3d, [%5d %5d %5d], %6d -> %6.4f %6.4f" % \
        #       (self.n_stage, self.n_micro, *self.fbw_cost, self.max_mem // self.f_mem, init_bubble / expected_time, bubble_rate), max_bubble)
        return schedule, end_time, max_bubble

    def print_details(self, end_time, print_scaling=1):
        for stage in range(self.n_stage):
            stage_str = ['.'] * int(max(end_time) / print_scaling)
            for _cat in range(3):
                for _chunk in range(2):
                    for _micro in range(self.n_micro):
                        _id = self.get_id(_cat, _chunk, stage, _micro)
                        if end_time[_id] < 0:
                            continue
                        end = int(end_time[_id] / print_scaling)
                        start = int((end_time[_id] - self.fbw_cost[_cat]) / print_scaling)
                        for j in range(start, end):
                            if j == start or j == end - 1:
                                stage_str[j] = "FfBbWw"[_cat * 2 + _chunk]
                            elif j == start + 1:
                                if _micro >= 10:
                                    stage_str[j] = str(_micro // 10)
                                else:
                                    stage_str[j] = str(_micro)
                            elif j == start + 2 and _micro >= 10:
                                stage_str[j] = str(_micro % 10)
                            else:
                                stage_str[j] = "-"
            _str = ""
            for _c in stage_str:
                _str += _c
            print(_str)

    def create_schedule(self, config):
        schedule, end_time, max_bubble = None, None, None
        expected_time = sum(self.fbw_cost) * self.n_micro * 2
        for fill_b in [True, False]:
            for fill_f in [True, False]:
                _schedule, _end_time, _max_bubble = self.try_v_schedule(
                    fill_b=fill_b, fill_f=fill_f
                )
                # print("")
                if max_bubble is None or _max_bubble < max_bubble:
                    max_bubble = _max_bubble
                    schedule = _schedule
                    end_time = _end_time

        bubble_rate = max_bubble / (expected_time + max_bubble)
        print("%2d %3d, [%5d %5d %5d %5d], %6d -> %6.4f" % \
              (self.n_stage, self.n_micro, *self.fbw_cost, self.c_cost, self.max_mem // self.f_mem, bubble_rate))

        local_order = [[] for _ in range(self.n_stage)]
        for stage in range(self.n_stage):
            for _cat_, _chunk_, _micro_ in schedule[stage]:
                chunk_index = _chunk_
                chunk = _chunk_ if _cat_ == 0 \
                    else config.max_chunks - 1 - _chunk_
                if _cat_ in (0, 1):
                    assert config.max_chunks == 2
                else:
                    assert _cat_ == 2
                layer_group_idx = config.n_stages * chunk
                if chunk % 2 == 0:
                    layer_group_idx += stage
                else:
                    layer_group_idx += config.n_stages - 1 - stage
                local_order[stage].append(ScheduledNode(
                    type=[F, B, W][_cat_],
                    chunk=chunk,
                    stage=stage,
                    microbatch=_micro_,
                    layer_group_idx=layer_group_idx,
                ))
        return local_order
