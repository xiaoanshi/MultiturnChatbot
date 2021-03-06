# -*- coding:utf-8 -*-
import itertools
import re
import unicodedata
import torch
import os
import json
from utils.vocab import *


# Turn a Unicode string to plain ASCII, thanks to https://stackoverflow.com/a/518232/2809427
def unicodeToAscii(s):
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


# Lowercase, trim, and remove non-letter characters
def normalizeString(s):
    s = unicodeToAscii(s.lower().strip())
    s = re.sub(r"([.!?])", r" \1", s)
    s = re.sub(r"[^a-zA-Z.!?]+", r" ", s)
    s = re.sub(r"\s+", r" ", s).strip()
    return s


# Read query/response pairs and return a voc object
def readVocs(trainfile, datafile, corpus_name):
    print("Reading lines...")
    # Read the file and split into lines
    lines = open(trainfile, encoding='utf-8').readlines()
    sentences = [[s for s in line[:-1].split('\t')[:-1]] for line in open(datafile, encoding='utf-8').readlines()]
    # sentences = [[normalizeString(s) for s in line.split('\t')] for line in open(datafile, encoding='utf-8').readlines()]
    # Split every line into pairs and normalize
    pairs = []
    graphs = []
    for l in lines:
        s = l[:-1].split('\t')
        assert len(s) == 3
        pairs.append([s[0], s[1]])
        graphs.append([int(node) for node in s[2].split(' ')])

    # pairs = [[s for s in l[:-1].split('\t')[:-1]] for l in lines]
    # pairs = [[normalizeString(s) for s in l.split('\t')] for l in lines]
    voc = Voc(corpus_name)
    return voc, pairs, sentences,graphs


# Returns True iff both sentences in a pair 'p' are under the MAX_LENGTH threshold
def filterPair(p, MAX_LENGTH):
    # Input sequences need to preserve the last word for EOS token
    return len(p[0].split(' ')) < MAX_LENGTH and len(p[1].split(' ')) < MAX_LENGTH


# Filter pairs using filterPair condition
def filterPairs(pairs, MAX_LENGTH):
    return [pair for pair in pairs if filterPair(pair, MAX_LENGTH)]


# Using the functions defined above, return a populated voc object and pairs list
def loadPrepareData(corpus, corpus_name, trainfile, datafile, save_dir, MAX_LENGTH):
    print("Start preparing training data ...")
    voc, pairs, sentences, graphs = readVocs(trainfile, datafile, corpus_name)
    print(str(len(pairs)) + ' ' + str(len(graphs)))
    assert len(pairs) == len(graphs)
    for i, pair in enumerate(pairs):
        pair.append(graphs[i])
    print("Read {!s} sentence pairs".format(len(pairs)))
    pairs = filterPairs(pairs, MAX_LENGTH)
    print("Trimmed to {!s} sentence pairs".format(len(pairs)))
    print("Counting words...")
    for sentence in sentences:
        voc.addSentence(sentence[0])
        voc.addSentence(sentence[0])
    # for pair in pairs:
    #     voc.addSentence(pair[0])
    #     voc.addSentence(pair[1])
    print("Counted words:", voc.num_words)
    return voc, pairs


def trimRareWords(voc, pairs, MIN_COUNT=0):
    # Trim words used under the MIN_COUNT from the voc
    voc.trim(MIN_COUNT)
    # Filter out pairs with trimmed words
    keep_pairs = []
    for pair in pairs:
        input_sentence = pair[0]
        output_sentence = pair[1]
        keep_input = True
        keep_output = True
        # Check input sentence
        for word in input_sentence.split(' '):
            if word not in voc.word2index:
                keep_input = False
                break
        # Check output sentence
        for word in output_sentence.split(' '):
            if word not in voc.word2index:
                keep_output = False
                break

        # Only keep pairs that do not contain trimmed word(s) in their input or output sentence
        if keep_input and keep_output:
            keep_pairs.append(pair)

    print("Trimmed from {} pairs to {}, {:.4f} of total".format(len(pairs), len(keep_pairs), len(keep_pairs) / len(pairs)))
    return keep_pairs


def indexesFromSentence(voc, sentence):
    return [voc.word2index[word] if word in voc.word2index else UNK_token for word in sentence.split(' ')] + [EOS_token]


def zeroPadding(l, fillvalue=PAD_token):
    return list(itertools.zip_longest(*l, fillvalue=fillvalue))


def binaryMatrix(l, value=PAD_token):
    m = []
    for i, seq in enumerate(l):
        m.append([])
        for token in seq:
            if token == PAD_token:
                m[i].append(0)
            else:
                m[i].append(1)
    return m


# Returns padded input sequence tensor and lengths
def inputVar(l, voc, gnn_model, graph_batch):
    assert len(l) == len(graph_batch)
    indexes_batch = []
    lengths = []
    graph_list = []
    for i, sentence in enumerate(l):
        graph = graph_batch[i]
        if graph:
            embs = gnn_model[0](graph)
            emb = embs.sum(0)
        else:
            emb = torch.zeros(128)
        emb = list(emb)
        graph_list.append(emb)
        indexes = indexesFromSentence(voc, sentence)
        # print(len(indexes))
        # indexes = emb + indexes
        indexes_batch.append(indexes)
        lengths.append(len(indexes))
        # emb_batch.append(emb.detach().numpy())
    # for sentence in l:
    #     nodes = []
    #     tokens = sentence.split(' ')
    #     for token in tokens:
    #         if token in sent2idx.keys():
    #             nodes.append(sent2idx[token])
    #     if nodes:
    #         embs = gnn_model[0](nodes)
    #         emb = embs.sum(0)
    #     else:
    #         emb = torch.zeros(128)
    #     emb_batch.append(emb.detach().numpy())
    # indexes_batch = [indexesFromSentence(voc, sentence) for sentence in l]
    # print(len(indexes_batch[0]))
    # print(lengths[0])
    lengths = torch.LongTensor(lengths)
    padList = zeroPadding(indexes_batch)
    padVar = torch.LongTensor(padList)
    graph_embs = torch.FloatTensor(graph_list)
    return padVar, lengths, graph_embs


# Returns padded target sequence tensor, padding mask, and max target length
def outputVar(l, voc, gnn_model, graph_batch):
    assert len(l) == len(graph_batch)
    indexes_batch = []
    graph_list = []
    for i, sentence in enumerate(l):
        graph = graph_batch[i]
        if graph:
            embs = gnn_model[0](graph)
            emb = embs.sum(0)
        else:
            emb = torch.zeros(128)
        emb = list(emb)
        graph_list.append(emb)
        indexes = indexesFromSentence(voc, sentence)
        # indexes = emb + indexes
        indexes_batch.append(indexes)
    # for sentence in l:
    #     nodes = []
    #     tokens = sentence.split(' ')
    #     for token in tokens:
    #         if token in sent2idx.keys():
    #             nodes.append(sent2idx[token])
    #     if nodes:
    #         embs = gnn_model[0](nodes)
    #         emb = embs.sum(0)
    #     else:
    #         emb = torch.zeros(128)
    #     emb_batch.append(emb.detach().numpy())
    # indexes_batch = [indexesFromSentence(voc, sentence) for sentence in l]
    max_target_len = max([len(indexes) for indexes in indexes_batch])
    padList = zeroPadding(indexes_batch)
    mask = binaryMatrix(padList)
    mask = torch.ByteTensor(mask)
    padVar = torch.LongTensor(padList)
    graph_embs = torch.FloatTensor(graph_list)
    return padVar, mask, max_target_len, graph_embs


# Returns all items for a given batch of pairs
def batch2TrainData(voc, pair_batch, gnn_model):
    pair_batch.sort(key=lambda x: len(x[0].split(" ")), reverse=True)
    input_batch, output_batch, graph_batch = [], [], []
    for pair in pair_batch:
        input_batch.append(pair[0])
        output_batch.append(pair[1])
        graph_batch.append(pair[2])
    inp, lengths, in_graph = inputVar(input_batch, voc, gnn_model, graph_batch)
    output, mask, max_target_len, out_graph = outputVar(output_batch, voc, gnn_model, graph_batch)
    return inp, lengths, output, mask, max_target_len, in_graph, out_graph


def maskNLLLoss(inp, target, mask, device):
    nTotal = mask.sum()
    crossEntropy = -torch.log(torch.gather(inp, 1, target.view(-1, 1)).squeeze(1))
    loss = crossEntropy.masked_select(mask).mean()
    loss = loss.to(device)
    return loss, nTotal.item()


def writeParaLog(opts, time):
    log_path = 'log/para/'
    if not os.path.exists(log_path):
        os.mkdir(log_path)
    with open(log_path + time + '.json', "w") as f:
        json.dump(opts.__str__(), f)
