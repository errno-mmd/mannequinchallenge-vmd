import os
import numpy as np
import logging
import cv2
import shutil
import json
import copy
import sys
import re
from matplotlib import pyplot as plt
import imageio
from collections import Counter
from PIL import Image

# ファイル出力ログ用
file_logger = logging.getLogger("message").getChild(__name__)
logger = logging.getLogger("__main__").getChild(__name__)

level = {0: logging.ERROR,
            1: logging.WARNING,
            2: logging.INFO,
            3: logging.DEBUG}

# 人物ソート
def exec(pred_depth_ary, pred_depth_support_ary, pred_image_ary, video_path, now_str, subdir, json_path, json_size, number_people_max, reverse_specific_dict, order_specific_dict, start_json_name, start_frame, end_frame_no, org_width, org_height, png_lib, scale, verbose):

    logger.warn("人物ソート開始 ---------------------------")

    # 前回情報
    past_pattern_datas = [{} for x in range(number_people_max)]

    cnt = 0
    cap = cv2.VideoCapture(video_path)
    while(cap.isOpened()):
        # 動画から1枚キャプチャして読み込む
        flag, frame = cap.read()  # Capture frame-by-frame

        # 深度推定のindex
        _idx = cnt - start_frame
        _display_idx = cnt

        # 開始フレームより前は飛ばす
        if start_frame > cnt:
            cnt += 1
            continue

        # 終わったフレームより後は飛ばす
        # 明示的に終わりが指定されている場合、その時も終了する
        if flag == False or cnt >= json_size + start_frame or (end_frame_no > 0 and _idx >= end_frame_no):
            break

        # 開始シーンのJSONデータを読み込む
        file_name = re.sub(r'\d{12}', "{0:012d}".format(cnt), start_json_name)
        _file = os.path.join(json_path, file_name)
        try:
            data = json.load(open(_file))
        except Exception as e:
            logger.warning("JSON読み込み失敗のため、空データ読み込み, %s %s", _file, e)
            data = json.load(open("json/all_empty_keypoints.json"))

        for i in range(len(data["people"]), number_people_max):
            # 足りない分は空データを埋める
            data["people"].append(json.load(open("json/one_keypoints.json")))

        logger.info("＊＊＊人体別処理: iidx: %s file: %s --------", _idx, file_name)

        # フレームイメージをオリジナルのサイズで保持(色差用)
        frame_img = np.array(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), dtype=np.float32)

        # 前フレームと出来るだけ近い位置のINDEX順番を計算する
        sorted_idxs, now_pattern_datas, normal_pattern_datas = calc_sort_and_direction(_idx, reverse_specific_dict, order_specific_dict, number_people_max, past_pattern_datas, data, pred_depth_ary[_idx], pred_depth_support_ary[_idx], frame_img)

        # 出力する
        output_sorted_data(_idx, _display_idx, number_people_max, sorted_idxs, now_pattern_datas, data, pred_depth_ary[_idx], json_path, now_str, file_name, reverse_specific_dict, order_specific_dict)

        # 画像保存
        save_image(_idx, pred_image_ary, frame_img, number_people_max, sorted_idxs, now_pattern_datas, subdir, cnt, png_lib, scale, verbose)

        # 今回のノーマルを前回分に置き換え
        past_pattern_datas = normal_pattern_datas

        # インクリメント
        cnt += 1

# 前フレームと出来るだけ近い位置のINDEX順番を計算する
def calc_sort_and_direction(_idx, reverse_specific_dict, order_specific_dict, number_people_max, past_pattern_datas, data, pred_depth, pred_depth_support, frame_img):
    if _idx == 0:
        # 今回情報
        now_pattern_datas = [{} for x in range(number_people_max)]

        # ソート順
        sorted_idxs = [-1 for x in range(number_people_max)]

        # 最初はインデックスの通りに並べる
        for pidx in range(number_people_max):
            sorted_idxs[pidx] = pidx

        # パターンはノーマルで生成        
        for _pidx in range(number_people_max):
            # パターン別のデータ
            now_pattern_datas[_pidx] = {"pidx": _pidx, "in_idx": _pidx, "pattern": OPENPOSE_NORMAL["pattern"], 
                "x": [0 for x in range(18)], "y": [0 for x in range(18)], "conf": [0 for x in range(18)], 
                "depth": [0 for x in range(18)], "depth_support": [], "color": [0 for x in range(18)]}

            # 1人分の関節位置データ
            now_xyc = data["people"][_pidx]["pose_keypoints_2d"]

            for o in range(0,len(now_xyc),3):
                oidx = int(o/3)
                now_pattern_datas[_pidx]["x"][oidx] = now_xyc[OPENPOSE_NORMAL[oidx]*3]
                now_pattern_datas[_pidx]["y"][oidx] = now_xyc[OPENPOSE_NORMAL[oidx]*3+1]
                now_pattern_datas[_pidx]["conf"][oidx] = now_xyc[OPENPOSE_NORMAL[oidx]*3+2]
                now_pattern_datas[_pidx]["depth"][oidx] = pred_depth[_pidx][OPENPOSE_NORMAL[oidx]]

                # 色情報
                if 0 <= int(now_xyc[o+1]) < frame_img.shape[0] and 0 <= int(now_xyc[o]) < frame_img.shape[1]:
                    now_pattern_datas[_pidx]["color"][oidx] = frame_img[int(now_xyc[o+1]),int(now_xyc[o])]
                else:
                    now_pattern_datas[_pidx]["color"][oidx] = np.array([0,0,0])

            # 深度補佐データ        
            now_pattern_datas[_pidx]["depth_support"] = pred_depth_support

        # 前回データはそのまま
        return sorted_idxs, now_pattern_datas, now_pattern_datas
    else:
        # ソートのための準備
        pattern_datas = prepare_sort(_idx, number_people_max, data, pred_depth, pred_depth_support, frame_img)

        if _idx in order_specific_dict:
            # 順番が指定されている場合、適用

            sorted_idxs = copy.deepcopy(order_specific_dict[_idx])

            # 今回のパターン結果
            now_pattern_datas = [[] for x in range(len(past_pattern_datas))]
            # ノーマルパターン結果
            normal_pattern_datas = [[] for x in range(len(past_pattern_datas))]

            # 人物INDEXが定まったところで、向きを再確認する
            now_pattern_datas, normal_pattern_datas = calc_direction_frame(_idx, number_people_max, past_pattern_datas, pattern_datas, sorted_idxs, 0.3)

            # 反転パターン
            if _idx in reverse_specific_dict:
                for _ridx in reverse_specific_dict[_idx]:
                    for _eidx, _sidx in enumerate(sorted_idxs):
                        if _ridx == _sidx:
                            pattern_type = 0
                            if reverse_specific_dict[_idx][_ridx] == "N":
                                pattern_type = 0
                            elif reverse_specific_dict[_idx][_ridx] == "R":
                                pattern_type = 1
                            elif reverse_specific_dict[_idx][_ridx] == "U":
                                pattern_type = 2
                            elif reverse_specific_dict[_idx][_ridx] == "L":
                                pattern_type = 3

                            now_pattern_datas[_eidx] = pattern_datas[_eidx*4+pattern_type]

            return sorted_idxs, now_pattern_datas, normal_pattern_datas
        else:
            # 1F目以降は前回と近接したINDEXに並び替える

            # 再頻出INDEXを算出
            return calc_sort_and_direction_frame(_idx, reverse_specific_dict, number_people_max, past_pattern_datas, pattern_datas)

# ソート順と向きを求める
def calc_sort_and_direction_frame(_idx, reverse_specific_dict, number_people_max, past_pattern_datas, pattern_datas):
    # ソート結果
    sorted_idxs = [-1 for x in range(number_people_max)]

    if number_people_max == 1:
        # 1人の場合はソート不要
        sorted_idxs = [0]
    else:
        all_most_common_idxs, conf_in_idx_list = calc_sort_frame(_idx, number_people_max, past_pattern_datas, pattern_datas, 0.2, 0.6)

        logger.debug("_idx: %s, all_most_common_idxs: %s", _idx, all_most_common_idxs)
        logger.debug("_idx: %s, conf_in_idx_list: %s", _idx, conf_in_idx_list)

        # 信頼度降順の人物INDEX
        conf_pidx_list = []
        for (in_idx, _) in conf_in_idx_list:
            if in_idx // 4 not in conf_pidx_list:
                conf_pidx_list.append(in_idx // 4)

        for _eidx, _pidx in enumerate(conf_pidx_list):
            # 4件ずつ区切って最頻出INDEXを配列に纏める
            now_nearest_idxs = [all_most_common_idxs[_pidx*4][0][0], all_most_common_idxs[_pidx*4+1][0][0], all_most_common_idxs[_pidx*4+2][0][0], all_most_common_idxs[_pidx*4+3][0][0]]

            # 最頻出INDEXを求める
            most_common_idxs = Counter(now_nearest_idxs).most_common()

            for mi in most_common_idxs:
                if mi[0] not in sorted_idxs:
                    # 最頻出INDEXがまだソート結果に入っていない場合
                    sorted_idxs[_pidx] = mi[0]
                    break
        
        existed_idxs = {}
        not_existed_idxs = []
        for _sidx in range(len(sorted_idxs)):
            if sorted_idxs[_sidx] >= 0:
                # ちゃんと値が入っていたら辞書保持
                existed_idxs[sorted_idxs[_sidx]] = _sidx
            else:
                not_existed_idxs.append(_sidx)

        # 値がない場合、まだ埋まってないのを先頭から
        _nidx = 0
        for _eidx in range(number_people_max):
            if _eidx not in existed_idxs:
                sorted_idxs[not_existed_idxs[_nidx]] = _eidx
                _nidx += 1

        logger.info("_idx: %s, sorted_idxs: %s", _idx, sorted_idxs)

    # 人物INDEXが定まったところで、向きを再確認する
    now_pattern_datas, normal_pattern_datas = calc_direction_frame(_idx, number_people_max, past_pattern_datas, pattern_datas, sorted_idxs, 0.3)

    # 反転パターン
    if _idx in reverse_specific_dict:
        for _ridx in reverse_specific_dict[_idx]:
            for _eidx, _sidx in enumerate(sorted_idxs):
                if _ridx == _sidx:
                    pattern_type = 0
                    if reverse_specific_dict[_idx][_ridx] == "N":
                        pattern_type = 0
                    elif reverse_specific_dict[_idx][_ridx] == "R":
                        pattern_type = 1
                    elif reverse_specific_dict[_idx][_ridx] == "U":
                        pattern_type = 2
                    elif reverse_specific_dict[_idx][_ridx] == "L":
                        pattern_type = 3

                    now_pattern_datas[_sidx] = pattern_datas[_eidx*4+pattern_type]

    for npd in now_pattern_datas:
        logger.info("_idx: %s, now_pattern_datas: pidx: %s, in_idx: %s, pattern: %s", _idx, npd["pidx"], npd["in_idx"], npd["pattern"])

    return sorted_idxs, now_pattern_datas, normal_pattern_datas


# 指定された方向（x, y, depth, color）に沿って、向きを計算する
def calc_direction_frame(_idx, number_people_max, past_pattern_datas, pattern_datas, sorted_idxs, th):
    # 今回のパターン結果
    now_pattern_datas = [[] for x in range(len(past_pattern_datas))]
    # ノーマルパターン結果
    normal_pattern_datas = [[] for x in range(len(past_pattern_datas))]

    for _eidx, _sidx in enumerate(sorted_idxs):
        # 直近INDEX
        now_nearest_idxs = []
        # 最頻出INDEX
        most_common_idxs = []

        for dimensional in ["x", "y"]:
            for _jidx in range(18):
                # 今回の該当関節データリスト
                now_per_joint_data = []
                for pt_data in pattern_datas[_eidx*4:_eidx*4+4]:
                    if pt_data["conf"][_jidx] >= th:
                        # 信頼度が足りている場合、該当辺の該当関節値を設定
                        now_per_joint_data.append(pt_data[dimensional][_jidx])
                    else:
                        # 信頼度が足りてない場合、とりあえずあり得ない値で引っかからないように
                        now_per_joint_data.append(999999999999)

                # 前回のチェック対象関節値    
                past_per_joint_value = past_pattern_datas[_sidx][dimensional][_jidx]

                now_nearest_idxs.extend(get_nearest_idxs(now_per_joint_data, past_per_joint_value))

        if len(now_nearest_idxs) > 0:
            most_common_idxs = Counter(now_nearest_idxs).most_common()

        now_pattern_datas[_sidx] = pattern_datas[_eidx*4+most_common_idxs[0][0]]
        # ノーマルパターン別保持
        normal_pattern_datas[_sidx] = pattern_datas[_eidx*4]
    
    for pd in [now_pattern_datas, normal_pattern_datas]:
        for (pd_one, ppd) in zip(pd, past_pattern_datas):
            for _jidx in range(18):
                if pd_one["depth"][_jidx] == 0:
                    # 信頼度が低い場合、前回データで上書き
                    pd_one["depth"][_jidx] = ppd["depth"][_jidx]

    return now_pattern_datas, normal_pattern_datas


# 指定された方向（x, y, depth, color）に沿って、ソート順を計算する
def calc_sort_frame(_idx, number_people_max, past_pattern_datas, pattern_datas, th, most_th):
    # 最頻出INDEX
    all_most_common_idxs = [[] for x in range(len(pattern_datas))]

    # 信頼度降順に並べ直す
    conf_in_idx_list = sorted(list(map(lambda x: (x["in_idx"], np.average(x["conf"])), pattern_datas)), key=lambda x: x[1], reverse=True)

    # 信頼度降順のin_idx順に埋めていく
    for (in_idx, _) in conf_in_idx_list:
        # 直近INDEX
        now_nearest_idxs = []
        # 最頻出INDEX
        most_common_idxs = []

        for dimensional in ["x", "y", "depth", "depth_support", "color"]:
            for _jidx in [1,2,3,5,6,8,9,10,11,12,13,1]:
                # 前回の該当関節データ
                past_per_joint_data = []
                for ppt_data in past_pattern_datas:
                    if ppt_data["conf"][_jidx] >= th:
                        # 信頼度が足りている場合、該当辺の該当関節値を設定
                        if dimensional == "depth_support":
                            # 深度サポート情報はとりあえず平均値だけとする
                            past_per_joint_data.append(np.median(ppt_data[dimensional]))
                            continue
                        else:
                            past_per_joint_data.append(ppt_data[dimensional][_jidx])
                    else:
                        # 信頼度が足りてない場合、とりあえずあり得ない値で引っかからないように
                        if dimensional == "color":
                            past_per_joint_data.append(np.full(3,999.9))
                        else:
                            past_per_joint_data.append(999999999999)

                # 今回チェックしようとしている関節値    
                per_joint_value = pattern_datas[in_idx][dimensional][_jidx] if dimensional != "depth_support" else np.median(pattern_datas[in_idx][dimensional])

                if dimensional == "color":
                    # 色の場合は組合せでチェック
                    now_nearest_idxs.extend(get_nearest_idx_ary(past_per_joint_data, per_joint_value))
                else:
                    now_nearest_idxs.extend(get_nearest_idxs(past_per_joint_data, per_joint_value))

            if len(now_nearest_idxs) > 0:
                most_common_idxs = Counter(now_nearest_idxs).most_common()

            # 頻出で振り分けた後、件数が足りない場合（全部どれか1つに寄せられている場合)
            if len(most_common_idxs) < len(past_pattern_datas):
                for c in range(len(past_pattern_datas)):
                    is_existed = False
                    for m, mci in enumerate(most_common_idxs):
                        if c == most_common_idxs[m][0]:
                            is_existed = True
                            break
                    
                    if is_existed == False:
                        # 存在しないインデックスだった場合、追加                 
                        most_common_idxs.append( (c, 0) )
            
            if most_common_idxs[0][1] / len(past_pattern_datas) >= most_th and len(all_most_common_idxs[in_idx]) == 0:
                # 再頻出INDEXの出現数が、全体の既定割合を超えていて、まだINDEXが設定されていなければ終了
                break

        all_most_common_idxs[in_idx] = most_common_idxs

    return all_most_common_idxs, conf_in_idx_list

def get_nearest_idxs(target_list, num):
    """
    概要: リストからある値に最も近い値のINDEXを返却する関数
    @param target_list: データ配列
    @param num: 対象値
    @return 対象値に最も近い値のINDEXの配列（同じ値がヒットした場合、すべて返す）
    """

    # logger.debug(target_list)
    # logger.debug(num)

    # リスト要素と対象値の差分を計算し最小値のインデックスを取得
    idx = np.abs(np.asarray(target_list) - num).argmin()

    result_idxs = []

    for i, v in enumerate(target_list):
        if v == target_list[idx]:
            result_idxs.append(i)

    return result_idxs

def get_nearest_idx_ary(target_list, num_ary):
    """
    概要: リストからある値に最も近い値のINDEXを返却する関数
    @param target_list: データ配列
    @param num: 対象値
    @return 対象値に最も近い値のINDEX
    """

    # logger.debug(target_list)
    # logger.debug(num)

    target_list2 = []
    for t in target_list:
        # 現在との色の差を絶対値で求める
        target_list2.append(np.round(np.abs(t - num_ary)))

    # logger.debug("num_ary: %s", num_ary)
    # logger.debug("target_list: %s", target_list)
    # logger.debug("target_list2: %s", target_list2)

    # リスト要素と対象値の差分を計算し最小値のインデックスを取得
    idxs = np.asarray(target_list2).argmin(axis=0)
    # logger.debug("np.asarray(target_list2).argmin(axis=0): %s", idxs)

    idx = np.argmax(np.bincount(idxs))
    # logger.debug("np.argmax(np.bincount(idxs)): %s", idx)

    result_idxs = []

    for i, v in enumerate(target_list2):
        if (v == target_list2[idx]).all():
            result_idxs.append(i)

    return result_idxs





# 通常INDEX
OPENPOSE_NORMAL = {"pattern": "normal", 0:0, 1:1, 2:2, 3:3, 4:4, 5:5, 6:6, 7:7, 8:8, 9:9, 10:10, 11:11, 12:12, 13:13, 14:14, 15:15, 16:16, 17:17, 18:18}
# 左右反転させたINDEX
OPENPOSE_REVERSE_ALL = {"pattern": "reverse", 0:0, 1:1, 2:5, 3:6, 4:7, 5:2, 6:3, 7:4, 8:11, 9:12, 10:13, 11:8, 12:9, 13:10, 14:15, 15:14, 16:17, 17:16, 18:18}
# 上半身のみ左右反転させたINDEX
OPENPOSE_REVERSE_UPPER = {"pattern": "up_reverse", 0:0, 1:1, 2:5, 3:6, 4:7, 5:2, 6:3, 7:4, 8:8, 9:9, 10:10, 11:11, 12:12, 13:13, 14:15, 15:14, 16:17, 17:16, 18:18}
# 下半身のみ左右反転させたINDEX
OPENPOSE_REVERSE_LOWER = {"pattern": "low_reverse", 0:0, 1:1, 2:2, 3:3, 4:4, 5:5, 6:6, 7:7, 8:11, 9:12, 10:13, 11:8, 12:9, 13:10, 14:14, 15:15, 16:16, 17:17, 18:18}

# ソートのための準備
# 人物データを、通常・全身反転・上半身反転・下半身反転の4パターンに分ける
def prepare_sort(_idx, number_people_max, data, pred_depth, pred_depth_support, frame_img):
    pattern_datas = [{} for x in range(number_people_max * 4)]

    for _pidx in range(number_people_max):
        for op_idx, op_idx_data in enumerate([OPENPOSE_NORMAL, OPENPOSE_REVERSE_ALL, OPENPOSE_REVERSE_UPPER, OPENPOSE_REVERSE_LOWER]):
            in_idx = (_pidx * 4) + op_idx

            # パターン別のデータ
            pattern_datas[in_idx] = {"pidx": _pidx, "in_idx": in_idx, "pattern": op_idx_data["pattern"], 
                "x": [0 for x in range(18)], "y": [0 for x in range(18)], "conf": [0 for x in range(18)], 
                "depth": [0 for x in range(18)], "depth_support": [], "color": [0 for x in range(18)]}

            # 1人分の関節位置データ
            now_xyc = data["people"][_pidx]["pose_keypoints_2d"]

            for o in range(0,len(now_xyc),3):
                oidx = int(o/3)
                pattern_datas[in_idx]["x"][oidx] = now_xyc[op_idx_data[oidx]*3]
                pattern_datas[in_idx]["y"][oidx] = now_xyc[op_idx_data[oidx]*3+1]
                
                # 信頼度調整値(キーと値が合ってない反転系は信頼度を少し下げる)
                conf_tweak = 0.0 if oidx == op_idx_data[oidx] else -0.1
                pattern_datas[in_idx]["conf"][oidx] = now_xyc[op_idx_data[oidx]*3+2] + conf_tweak

                # 深度情報
                pattern_datas[in_idx]["depth"][oidx] = pred_depth[_pidx][op_idx_data[oidx]]

                # 色情報
                if 0 <= int(now_xyc[o+1]) < frame_img.shape[0] and 0 <= int(now_xyc[o]) < frame_img.shape[1]:
                    pattern_datas[in_idx]["color"][oidx] = frame_img[int(now_xyc[o+1]),int(now_xyc[o])]
                else:
                    pattern_datas[in_idx]["color"][oidx] = np.array([0,0,0])

            # 深度補佐データ        
            pattern_datas[in_idx]["depth_support"] = pred_depth_support

            logger.debug(pattern_datas[in_idx])

        logger.debug(pattern_datas)

    return pattern_datas

# ソート順に合わせてデータを出力する
def output_sorted_data(_idx, _display_idx, number_people_max, sorted_idxs, now_pattern_datas, data, pred_depth, json_path, now_str, file_name, reverse_specific_dict, order_specific_dict):
    # 指定ありの場合、メッセージ追加
    if _idx in order_specific_dict:
        file_logger.warning("※※{0:05d}F目、順番指定 [{0}:{2}]".format( _idx, _display_idx, ','.join(map(str, sorted_idxs))))

    display_nose_pos = {}
    for _eidx, npd in enumerate(now_pattern_datas):
        # データがある場合、そのデータ
        display_nose_pos[_eidx] = [npd["x"][1], npd["y"][1]]

        # インデックス対応分のディレクトリ作成
        idx_path = '{0}/{1}_{3}_idx{2:02d}/json/{4}'.format(os.path.dirname(json_path), os.path.basename(json_path), _eidx+1, now_str, file_name)
        os.makedirs(os.path.dirname(idx_path), exist_ok=True)
        
        output_data = {"people": [{"pose_keypoints_2d": []}]}
        for (npd_x, npd_y, npd_conf) in zip(npd["x"], npd["y"], npd["conf"]):
            output_data["people"][0]["pose_keypoints_2d"].append(npd_x)
            output_data["people"][0]["pose_keypoints_2d"].append(npd_y)
            output_data["people"][0]["pose_keypoints_2d"].append(npd_conf)

        # 指定ありの場合、メッセージ追加
        reverse_specific_str = ""
        if _idx in reverse_specific_dict and _eidx in reverse_specific_dict[_idx]:
            reverse_specific_str = "【指定】"

        if npd["pattern"] == "reverse":
            file_logger.warning("※※{0:05d}F目 {2}番目の人物、全身反転 [{0}:{2},R]{3}".format( _idx, _display_idx, npd["pidx"], reverse_specific_str))
        elif npd["pattern"] == "up_reverse":
            file_logger.warning("※※{0:05d}F目 {2}番目の人物、上半身反転 [{0}:{2},U]{3}".format( _idx, _display_idx, npd["pidx"], reverse_specific_str))
        elif npd["pattern"] == "low_reverse":
            file_logger.warning("※※{0:05d}F目 {2}番目の人物、下半身反転 [{0}:{2},L]{3}".format( _idx, _display_idx, npd["pidx"], reverse_specific_str))
        else:
            if len(reverse_specific_str) > 0:
                file_logger.warning("※※{0:05d}F目 {2}番目の人物、反転なし [{0}:{2},N]{3}".format( _idx, _display_idx, npd["pidx"], reverse_specific_str))

        # 出力
        json.dump(output_data, open(idx_path,'w'), indent=4)

        # 深度データ
        depth_idx_path = '{0}/{1}_{3}_idx{2:02d}/depth.txt'.format(os.path.dirname(json_path), os.path.basename(json_path), _eidx+1, now_str)
        # 追記モードで開く
        depthf = open(depth_idx_path, 'a')
        # 一行分を追記
        depthf.write("{0}, {1},{2}\n".format(_display_idx, ','.join([ str(x) for x in npd["depth"] ]), ','.join([ str(x) for x in npd["depth_support"] ])))
        depthf.close()

    file_logger.warning("＊＊{0:05d}F目の出力順番: [{0}:{2}], 位置: {3}".format(_idx, _display_idx, ','.join(map(str, sorted_idxs)), sorted(display_nose_pos.items()) ))

# 深度画像を保存する
def save_image(_idx, pred_image_ary, frame_img, number_people_max, sorted_idxs, now_pattern_datas, subdir, cnt, png_lib, scale, verbose):
    # 深度画像保存 -----------------------
    if level[verbose] <= logging.INFO and len(pred_image_ary[_idx]) > 0:
        # Plot result
        plt.cla()
        plt.clf()
        ii = plt.imshow(pred_image_ary[_idx], interpolation='nearest')
        plt.colorbar(ii)

        # 散布図のようにして、出力に使ったポイントを明示
        DEPTH_COLOR = ["#33FF33", "#3333FF", "#FFFFFF", "#FFFF33", "#FF33FF", "#33FFFF", "#00FF00", "#0000FF", "#666666", "#FFFF00", "#FF00FF", "#00FFFF"]
        for _eidx, npd in enumerate(now_pattern_datas):
            for (npd_x, npd_y) in zip(npd["x"], npd["y"]):
                plt.scatter(npd_x * scale, npd_y * scale, s=5, c=DEPTH_COLOR[_eidx])

        plotName = "{0}/depth_{1:012d}.png".format(subdir, cnt)
        plt.savefig(plotName)
        logger.debug("Save: {0}".format(plotName))

        png_lib.append(imageio.imread(plotName))

        plt.close()
