# coding=utf-8
# 2020.08.28 - Changed regular evaluation to evaluation with adaptive width and depth
#              Huawei Technologies Co., Ltd <houlu3@huawei.com>
# Copyright (c) 2020, Huawei Technologies Co., Ltd.  All rights reserved.
# Copyright 2018 The Google AI Language Team Authors and The HuggingFace Inc. team.
# Copyright (c) 2018, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
""" Finetuning the library models for sequence classification on GLUE (Bert, XLM, XLNet, RoBERTa)."""

from __future__ import absolute_import, division, print_function

import argparse
import logging
import os
import random
import numpy as np
import torch
from torch.utils.data import (DataLoader, RandomSampler, SequentialSampler, TensorDataset)
from tqdm import tqdm

from transformers import (BertConfig,
                                  BertForSequenceClassification, BertTokenizer,
                                  RobertaConfig,
                                  RobertaForSequenceClassification,
                                  RobertaTokenizer,
                          )

from transformers import glue_compute_metrics as compute_metrics
from transformers import glue_output_modes as output_modes
from transformers import glue_processors as processors
from transformers import glue_convert_examples_to_features as convert_examples_to_features


logger = logging.getLogger(__name__)


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, input_ids, input_mask, segment_ids, label_id, seq_length=None):
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.seq_length = seq_length
        self.label_id = label_id


def _truncate_seq_pair(tokens_a, tokens_b, max_length):
    """Truncates a sequence pair in place to the maximum length."""
    while True:
        total_length = len(tokens_a) + len(tokens_b)
        if total_length <= max_length:
            break
        if len(tokens_a) > len(tokens_b):
            tokens_a.pop()
        else:
            tokens_b.pop()


def convert_examples_to_features_test(examples, label_list, max_seq_length,
                                 tokenizer, output_mode):
    """Loads a data file into a list of `InputBatch`s."""

    label_map = {label: i for i, label in enumerate(label_list)}

    features = []
    for (ex_index, example) in enumerate(examples):
        if ex_index % 10000 == 0:
            logger.info("Writing example %d of %d" % (ex_index, len(examples)))

        tokens_a = tokenizer.tokenize(example.text_a)

        tokens_b = None
        if example.text_b:
            tokens_b = tokenizer.tokenize(example.text_b)
            _truncate_seq_pair(tokens_a, tokens_b, max_seq_length - 3)
        else:
            if len(tokens_a) > max_seq_length - 2:
                tokens_a = tokens_a[:(max_seq_length - 2)]

        tokens = ["[CLS]"] + tokens_a + ["[SEP]"]
        segment_ids = [0] * len(tokens)

        if tokens_b:
            tokens += tokens_b + ["[SEP]"]
            segment_ids += [1] * (len(tokens_b) + 1)

        input_ids = tokenizer.convert_tokens_to_ids(tokens)
        input_mask = [1] * len(input_ids)
        seq_length = len(input_ids)

        padding = [0] * (max_seq_length - len(input_ids))
        input_ids += padding
        input_mask += padding
        segment_ids += padding

        assert len(input_ids) == max_seq_length
        assert len(input_mask) == max_seq_length
        assert len(segment_ids) == max_seq_length
        try:
            if output_mode == "classification":
                label_id = label_map[example.label]
            elif output_mode == "regression":
                label_id = float(example.label)
            else:
                raise KeyError(output_mode)
        except:
            label_id = 0

        if ex_index < 1:
            logger.info("*** Example ***")
            logger.info("guid: %s" % (example.guid))
            logger.info("tokens: %s" % " ".join(
                [str(x) for x in tokens]))
            logger.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
            logger.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
            logger.info(
                "segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
            logger.info("label: {}".format(example.label))
            logger.info("label_id: {}".format(label_id))

        features.append(
            InputFeatures(input_ids=input_ids,
                          input_mask=input_mask,
                          segment_ids=segment_ids,
                          label_id=label_id,
                          seq_length=seq_length))
    return features


MODEL_CLASSES = {
    'bert': (BertConfig, BertForSequenceClassification, BertTokenizer),
    'roberta': (RobertaConfig, RobertaForSequenceClassification, RobertaTokenizer),
}


def set_seed(args):
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)


def evaluate(args, model, tokenizer, prefix=""):
    # Loop to handle MNLI double evaluation (matched, mis-matched)
    eval_task_names = ("mnli", "mnli-mm") if args.task_name == "mnli" else (args.task_name,)

    results = {}
    for eval_task in eval_task_names:
        eval_dataset = load_and_cache_examples(args, eval_task, tokenizer, evaluate=True)

        eval_output_dir = os.path.join(args.output_dir,
                                       args.model_type + '_' + args.width_mult + '_' + args.depth_mult + '_eval')
        if not os.path.exists(eval_output_dir):
                # and args.local_rank in [-1, 0]:
            os.makedirs(eval_output_dir)

        args.eval_batch_size = args.per_gpu_eval_batch_size * max(1, args.n_gpu)
        # Note that DistributedSampler samples randomly
        eval_sampler = SequentialSampler(eval_dataset)
        eval_dataloader = DataLoader(eval_dataset, sampler=eval_sampler, batch_size=args.eval_batch_size)

        logger.info("***** Running evaluation {} *****".format(prefix))
        logger.info("  Num examples = %d", len(eval_dataset))
        logger.info("  Batch size = %d", args.eval_batch_size)
        eval_loss = 0.0
        nb_eval_steps = 0
        preds = None
        out_label_ids = None
        max_eval_count = 9999
        count = 0
        # lwg: measure time
        import time 
        for batch in tqdm(eval_dataloader, desc="Evaluating"):
            model.eval()
            batch = tuple(t.to(args.device) for t in batch)

            with torch.no_grad():
                inputs = {'input_ids':      batch[0],
                          'attention_mask': batch[1],
                          'labels':         batch[3]}
                if args.model_type != 'distilbert':
                    inputs['token_type_ids'] = batch[2] if args.model_type in ['bert', 'xlnet'] else None  # XLM, DistilBERT and RoBERTa don't use segment_ids
                # the following print function is to output original sentence for the visualization, wrt. batch_size=1
                text = tokenizer.decode(inputs['input_ids'][0].cpu().numpy(), skip_special_tokens=True)
                n_tokens = np.count_nonzero(inputs['input_ids'][0].cpu().numpy())
                #print(text)
                #print(tokenizer.decode(inputs['input_ids'][0].cpu().numpy()))
                # lwg: torchprof...
                #paths = [('BertForSequenceClassification', 'bert', 'encoder', 'layer', '0', 'attention')]
                #with torchprof.Profile(model, use_cuda=False, paths=paths) as prof:
                '''
                import torchprof 
                with torchprof.Profile(model, use_cuda=False) as prof:
                    start = time.time()
                    outputs = model(**inputs)
                    end = time.time()
                #print(prof.display(show_events=True))
                #print(prof.display(show_events=False))
                '''
                start = time.time()
                outputs = model(**inputs)
                end = time.time()
                print("%d,%.3f" % (n_tokens, (end-start) * 1000))
                tmp_eval_loss, logits = outputs[:2]

                eval_loss += tmp_eval_loss.mean().item()

                # lwg: terminate automatically 
                count += 1
                if count >= max_eval_count:
                    break

            nb_eval_steps += 1
            if preds is None:
                preds = logits.detach().cpu().numpy()
                out_label_ids = inputs['labels'].detach().cpu().numpy()
            else:
                preds = np.append(preds, logits.detach().cpu().numpy(), axis=0)
                out_label_ids = np.append(out_label_ids, inputs['labels'].detach().cpu().numpy(), axis=0)

        if args.output_mode == "classification":
            preds = np.argmax(preds, axis=1)
        elif args.output_mode == "regression":
            preds = np.squeeze(preds)
        result = compute_metrics(eval_task, preds, out_label_ids)
        if eval_task == 'mnli-mm':
            results.update({'acc_mm':result['acc']})
        else:
            results.update(result)

        output_eval_file = os.path.join(eval_output_dir, "eval_results_{0}.txt".format(eval_task))
        with open(output_eval_file, "a") as writer:
            logger.info("***** Eval results {} *****".format(prefix))
            for key in sorted(result.keys()):
                logger.info("%s = %s (emb bits = %d, enc bits = %d)\n" % (key, str(result[key]), args.emb, args.enc))
                writer.write("%s = %s (emb bits = %d, enc bits = %d)\n" % (key, str(result[key]), args.emb, args.enc))
            writer.write("\n")
    return results


def get_tensor_data(output_mode, features):
    if output_mode == "classification":
        all_label_ids = torch.tensor([f.label_id for f in features], dtype=torch.long)
    elif output_mode == "regression":
        all_label_ids = torch.tensor([f.label_id for f in features], dtype=torch.float)

    all_seq_lengths = torch.tensor([f.seq_length for f in features], dtype=torch.long)
    all_input_ids = torch.tensor([f.input_ids for f in features], dtype=torch.long)
    all_input_mask = torch.tensor([f.input_mask for f in features], dtype=torch.long)
    all_segment_ids = torch.tensor([f.segment_ids for f in features], dtype=torch.long)
    tensor_data = TensorDataset(all_input_ids, all_input_mask, all_segment_ids,
                                all_label_ids, all_seq_lengths)
    return tensor_data, all_label_ids


def load_and_cache_examples_test(args, task, tokenizer):

    processor = processors[task]()
    output_mode = output_modes[task]

    label_list = processor.get_labels()
    if task in ['mnli', 'mnli-mm'] and args.model_type in ['roberta']:
        label_list[1], label_list[2] = label_list[2], label_list[1]
    examples = processor.get_test_examples(args.data_dir)
    features = convert_examples_to_features_test(examples, label_list, args.max_seq_length, tokenizer, output_mode)
    data, labels = get_tensor_data(output_mode, features)
    return data, label_list


def load_and_cache_examples(args, task, tokenizer, evaluate=False):

    processor = processors[task]()
    output_mode = output_modes[task]

    logger.info("Creating features from dataset file at %s", args.data_dir)
    label_list = processor.get_labels()
    if task in ['mnli', 'mnli-mm'] and args.model_type in ['roberta']:
        # HACK(label indices are swapped in RoBERTa pretrained model)
        label_list[1], label_list[2] = label_list[2], label_list[1]
    examples = processor.get_dev_examples(args.data_dir) if evaluate else processor.get_train_examples(args.data_dir)

    features = convert_examples_to_features(examples,
                                            tokenizer,
                                            label_list=label_list,
                                            max_length=args.max_seq_length,
                                            output_mode=output_mode,
                                            pad_on_left=bool(args.model_type in ['xlnet']),                 # pad on the left for xlnet
                                            pad_token=tokenizer.convert_tokens_to_ids([tokenizer.pad_token])[0],
                                            pad_token_segment_id=4 if args.model_type in ['xlnet'] else 0,
    )

    # Convert to Tensors and build dataset
    all_input_ids = torch.tensor([f.input_ids for f in features], dtype=torch.long)
    all_attention_mask = torch.tensor([f.attention_mask for f in features], dtype=torch.long)
    all_token_type_ids = torch.tensor([f.token_type_ids for f in features], dtype=torch.long)
    if output_mode == "classification":
        all_labels = torch.tensor([f.label for f in features], dtype=torch.long)
    elif output_mode == "regression":
        all_labels = torch.tensor([f.label for f in features], dtype=torch.float)

    dataset = TensorDataset(all_input_ids, all_attention_mask, all_token_type_ids, all_labels)
    return dataset


def main():
    parser = argparse.ArgumentParser()

    # Required parameters
    parser.add_argument("--data_dir", default=None, type=str, required=True,
                        help="The input data dir. Should contain the .tsv files (or other data files) for the task.")
    parser.add_argument("--model_type", default=None, type=str, required=True,
                        help="Model type selected in the list: " + ", ".join(MODEL_CLASSES.keys()))
    parser.add_argument("--task_name", default=None, type=str, required=True,
                        help="The name of the task to train selected in the list: " + ", ".join(processors.keys()))
    parser.add_argument("--output_dir", default=None, type=str, required=True,
                        help="The output directory where the model predictions will be written.")
    parser.add_argument("--max_seq_length", default=128, type=int,
                        help="The maximum total input sequence length after tokenization. Sequences longer "
                             "than this will be truncated, sequences shorter will be padded.")
    parser.add_argument("--do_lower_case", default=True,
                        help="Set this flag if you are using an uncased model.")
    parser.add_argument("--per_gpu_eval_batch_size", default=128, type=int,
                        help="Batch size per GPU/CPU for evaluation.")
    parser.add_argument("--no_cuda", action='store_true',
                        help="Avoid using CUDA when available")
    parser.add_argument('--seed', type=int, default=42,
                        help="random seed for initialization")
    parser.add_argument("--model_dir", type=str,
                        help="The teacher model dir.")
    parser.add_argument('--depth_mult', type=str, default='1.',
                        help="the possible depths used for training, e.g., '1.' is for default")
    parser.add_argument('--width_mult', type=str, default='1.',
                        help="the possible depths used for training, e.g., '1.' is for default")

    # lwg tune encoder/embedding bits
    parser.add_argument('--emb', type=int, default=0,
                        help="Embedding quantization bits")
    parser.add_argument('--enc', type=int, default=0,
                        help="Encoder quantization bits")
    
    args = parser.parse_args()
    # lwg: don't use best
    #args.model_dir = os.path.join(args.model_dir, 'best')
    bits_conf = str(args.emb) + '_' + str(args.enc)
    #bits_conf = ''
    model_root = args.model_dir
    args.model_dir = os.path.join(args.model_dir, bits_conf)
    #device = torch.device("cuda" if torch.cuda.is_available() and not args.no_cuda else "cpu")
    device = torch.device("cpu")
    args.n_gpu = torch.cuda.device_count()
    args.device = device

    # Setup logging
    logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                        datefmt = '%m/%d/%Y %H:%M:%S',
                        level = logging.INFO)
    logger.warning("device: %s, n_gpu: %s", device, args.n_gpu, )

    # Set seed
    set_seed(args)

    # Prepare GLUE task
    args.task_name = args.task_name.lower()
    if args.task_name not in processors:
        raise ValueError("Task not found: %s" % (args.task_name))

    processor = processors[args.task_name]()
    args.output_mode = output_modes[args.task_name]
    label_list = processor.get_labels()
    num_labels = len(label_list)

    args.model_type = args.model_type.lower()
    config_class, model_class, tokenizer_class = MODEL_CLASSES[args.model_type]

    # lwg: load one base model, specified by enc_bit, emb_bit 
    config = config_class.from_pretrained(args.model_dir, num_labels=num_labels, finetuning_task=args.task_name)
    tokenizer = tokenizer_class.from_pretrained(args.model_dir, do_lower_case=args.do_lower_case)
    model = model_class.from_pretrained(args.model_dir, config=config)
    model.to(args.device)
    model.bert.encoder.update_bit(args.enc)

    # load 2 - 6 bits
    models = []
    enc_bits = np.arange(2, 7)

    def load_quantized_model(enc_bit, emb_bit=3):
        bits_conf = str(emb_bit) + '_' + str(enc_bit)
        print("loading ", bits_conf, "...")
        model_dir = os.path.join(model_root, bits_conf)
        config = config_class.from_pretrained(model_dir, num_labels=num_labels, finetuning_task=args.task_name)
        tokenizer = tokenizer_class.from_pretrained(model_dir, do_lower_case=args.do_lower_case)
        _model = model_class.from_pretrained(model_dir, config=config)
        _model.to(args.device)
        _model.bert.encoder.update_bit(enc_bit)
        model.bert.encoder.add_quantized_model(_model.bert.encoder)
        # 64 dim per head
        #print(model.bert.encoder.layer[0].attention.self.attention_head_size)
        return _model

    """
    for bit in enc_bits:
        m  = load_quantized_model(bit)
    """
    m  = load_quantized_model(2)
    m  = load_quantized_model(6)

    print(config)
    zero_model = BertForSequenceClassification(config=config)
    # model with all zero weights 
    zero_model.init_weights()
    zero_model.bert.encoder.update_bit(0)
    model.bert.encoder.add_quantized_model(zero_model.bert.encoder)
    load_quantized_model(32, 32)

    
    '''
    shard_conf1 = [0] * 12
    #shard_conf1[3] = 6 
    #shard_conf1[0] = 6 
    for i in range(12):
        model.bert.encoder.layer[i].attention.patch_attention_shards(shard_conf1)
        model.bert.encoder.layer[i].intermediate.patch_intermediate_shards(shard_conf1)
        model.bert.encoder.layer[i].output.patch_ffn_shards(shard_conf1)
    '''


    #print(model.bert.encoder.quantized)
    #return

    '''
    # quantize then save the model...
    if emb_bits != 0:
        model.bert.embeddings.quantize(emb_bits)
    if enc_bits != 1:
        model.bert.encoder.quantize(enc_bits)
    # dir_format ~/models/#emb_#enc 
    model_save_dir = os.path.join('/home/lwg/models/', str(emb_bits) + '_' + str(enc_bits))
    if not os.path.exists(model_save_dir):
                # and args.local_rank in [-1, 0]:
            os.makedirs(model_save_dir)
    model.save_pretrained(model_save_dir)
    tokenizer.save_vocabulary(model_save_dir)
    return
    '''
    def write_to_results(s):
        eval_output_dir = os.path.join(args.output_dir,
                                       args.model_type + '_' + args.width_mult + '_' + args.depth_mult + '_eval')
        output_eval_file = os.path.join(eval_output_dir, "eval_results_{0}.txt".format("ablation_upgrade"))
        with open(output_eval_file, "a") as writer:
            writer.write(s)
            writer.write('\n')

    write_to_results("eval begin...")


    """
    # ablation study of shard importance 
    base_conf  = [2]*12
    for l in range(0, 12):
        for i in range(0, 12):
            '''
            shard_conf2 = [32] * 12
            shard_conf2[i] = 0
            '''

            shard_conf2 = [2] * 12
            shard_conf2[i] = 32 

            model.bert.encoder.layer[l].attention.patch_attention_shards(shard_conf2)
            model.bert.encoder.layer[l].intermediate.patch_intermediate_shards(shard_conf2)
            model.bert.encoder.layer[l].output.patch_ffn_shards(shard_conf2)

            model.apply(lambda m: setattr(m, 'depth_mult', float(args.depth_mult)))
            model.apply(lambda m: setattr(m, 'width_mult', float(args.width_mult)))

            results = evaluate(args, model, tokenizer)
            print(shard_conf2)
            print(results)
            print("emb bits:", args)
            print("enc bits:", enc_bits)
            output = "%s: (%d,%d)" % (results, i, l)
            write_to_results(output)
        # reset prev layer before proceeding to the next
        model.bert.encoder.layer[l].attention.patch_attention_shards(base_conf)
        model.bert.encoder.layer[l].intermediate.patch_intermediate_shards(base_conf)
        model.bert.encoder.layer[l].output.patch_ffn_shards(base_conf)
    """

    # verify heuristics 

            
    def patch_layer_shard(l, conf):
        model.bert.encoder.layer[l].attention.patch_attention_shards(conf)
        model.bert.encoder.layer[l].intermediate.patch_intermediate_shards(conf)
        model.bert.encoder.layer[l].output.patch_ffn_shards(conf)

    def reset_model(bits):
        conf = [bits]*12
        for l in range(0, 12):
            patch_layer_shard(l, conf)

    # our heuristics: top 6 most important shards to 6-bit
    # from downgrade map: (11, 3), (10, 1), (10, 4), (10, 6), (10, 8), (10, 9) ---> does not work
    # from upgrade map: (0, 0), (0,3), (1, 5), (6, 1), (7, 0) (7, 8) ---> good performance
    reset_model(2)
    tmp_conf = [2]*12
    tmp_conf[0] = 6
    tmp_conf[3] = 6
    patch_layer_shard(0, tmp_conf)
    tmp_conf = [2]*12
    tmp_conf[5] = 6
    patch_layer_shard(1, tmp_conf)
    tmp_conf = [2]*12
    tmp_conf[1] = 6
    patch_layer_shard(6, tmp_conf)
    tmp_conf = [2]*12
    tmp_conf[0] = 6
    tmp_conf[8] = 6
    patch_layer_shard(7, tmp_conf)
    results = evaluate(args, model, tokenizer)
    output = "ours: %s" % (results)
    write_to_results(output)
    return

    #tmp_conf = [6, 6, 6, 6, 6, 6, 2, 2, 2, 2, 2, 2] 
    tmp_conf = [2, 2, 2, 2, 2, 2, 6, 6, 6, 6, 6, 6] 
    #tmp_conf = [2]*12 
    #tmp_conf[3] = 6
    patch_layer_shard(0, tmp_conf)
    #tmp_conf = [2, 6, 2, 2, 6, 2, 6, 2, 6, 6, 2, 2]
    #patch_layer_shard(10, tmp_conf)
    results = evaluate(args, model, tokenizer)
    output = "ours: %s" % (results)
    write_to_results(output)
    #reset_model(args.enc)
    #results = evaluate(args, model, tokenizer)


    '''
    model.apply(lambda m: setattr(m, 'depth_mult', float(args.depth_mult)))
    model.apply(lambda m: setattr(m, 'width_mult', float(args.width_mult)))

    results = evaluate(args, model, tokenizer)
    print(results)
    print("emb bits:", args)
    print("enc bits:", enc_bits)
    print(shard_conf1)
    '''


if __name__ == "__main__":
    main()
