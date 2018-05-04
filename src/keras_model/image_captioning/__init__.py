from argparse import ArgumentParser
from common.util import merge_dict
from functools import partial
import json
from keras_model.image_captioning.hyperparams import get_constants
from keras_model.image_captioning.model_setup import cnn_encoder_builder, decoder_builder, model_builder
from keras_model.image_captioning.util import caption_tokens_to_indices, check_after_training
from keras_model.image_captioning.util import generate_vocabulary, get_captions, get_pad_idx, load_embeddings
from keras_model.image_captioning.util import show_training_example, show_valid_example, train
import numpy as np
import os
import tensorflow as tf
from tensorflow.contrib import keras
import time
from zipfile import ZipFile

K = keras.backend
L = keras.layers

DATA_DIR = '../../../data/'

OUTPUT_DIR = '../../../output'

# Model architecture: CNN encoder and RNN decoder.
#   https://research.googleblog.com/2014/11/a-picture-is-worth-thousand-coherent.html

# Training data
# Takes 10 hours and 20 GB.
# train images: http://msvocds.blob.core.windows.net/coco2014/train2014.zip
# validation images: http://msvocds.blob.core.windows.net/coco2014/val2014.zip
# captions for both train and validation:
#   http://msvocds.blob.core.windows.net/annotations-1-0-3/captions_train-val2014.zip


def run(constant_overwrites):
    constants = merge_dict(get_constants(), constant_overwrites)
    data = load_embeddings()

    print('\ncheck embeddings shapes:')
    print('train:', data['img_embeds_train'].shape, len(data['img_filenames_train']))
    print('val:', data['img_embeds_val'].shape, len(data['img_filenames_val']))

    data = merge_dict(data, get_captions(data['img_filenames_train'], data['img_filenames_val']))

    print('\ncheck captions shapes:')
    print('train:', len(data['img_filenames_train']), len(data['captions_train']))
    print('val:', len(data['img_filenames_val']), len(data['captions_val']))

    vocab = generate_vocabulary(data['captions_train'])
    captions_indexed_train = caption_tokens_to_indices(data['captions_train'], vocab)
    captions_indexed_val = caption_tokens_to_indices(data['captions_val'], vocab)
    img_embed_size = data['img_embeds_train'].shape[1]
    vocab_size = len(vocab)

    print('')
    print('img_embed_size:', img_embed_size)
    print('vocab_size:', vocab_size)

    pad_idx = get_pad_idx(vocab)
    data = merge_dict(data, {
        'vocab': vocab,
        'vocab_inverse': {i: w for w, i in vocab.items()},
        'captions_indexed_train': np.array(captions_indexed_train),
        'captions_indexed_val': np.array(captions_indexed_val),
        'img_embed_size': img_embed_size,
        'vocab_size': vocab_size,
        'pad_idx': pad_idx
    })

    show_training_example(data['img_filenames_train'], data['captions_train'], example_idx=142)

    print('\npreview captions data:')
    print(json.dumps(data['captions_train'][:2], indent=4))

    # make sure you use correct argument in `caption_tokens_to_indices`
    assert len(caption_tokens_to_indices(data['captions_train'][:10], vocab)) == 10
    assert len(caption_tokens_to_indices(data['captions_train'][:5], vocab)) == 5

    # remember to reset your graph if you want to start building it from scratch!
    tf.reset_default_graph()
    tf.set_random_seed(42)
    sess = tf.InteractiveSession()

    writer = tf.summary.FileWriter(OUTPUT_DIR, sess.graph)

    decoder = decoder_builder(data, constants)
    # decoder = Decoder(data, constants)

    '''
    img_embed_bottleneck = constants['img_embed_bottleneck']
    lstm_units = constants['lstm_units']
    word_embed_size = constants['word_embed_size']
    logit_bottleneck = constants['logit_bottleneck']

    class decoder:

        # [batch_size, img_embed_size] of CNN image features
        img_embeds = tf.placeholder('float32', [None, img_embed_size])

        # [batch_size, time steps] of word ids
        sentences = tf.placeholder('int32', [None, None])

        # image embedding -> bottleneck to reduce the number of parameters
        img_embed_to_bottleneck = L.Dense(img_embed_bottleneck,
                                          input_shape=(None, img_embed_size),
                                          activation='elu')

        # image embedding bottleneck -> LSTM initial state
        img_embed_bottleneck_to_h0 = L.Dense(lstm_units,
                                             input_shape=(None, img_embed_bottleneck),
                                             activation='elu')

        # word -> embedding
        word_embed = L.Embedding(len(vocab), word_embed_size)

        # LSTM Cell (from TensorFlow)
        lstm = tf.nn.rnn_cell.LSTMCell(lstm_units)

        # LSTM output -> logits bottleneck to reduce model complexity
        token_logits_bottleneck = L.Dense(logit_bottleneck,
                                          input_shape=(None, lstm_units),
                                          activation="elu")

        # logits bottleneck -> logits for next token prediction
        token_logits = L.Dense(len(vocab),
                               input_shape=(None, logit_bottleneck))

        # Initial LSTM cell state of shape (None, lstm_units),
        # condition on `img_embeds` placeholder
        c0 = h0 = img_embed_bottleneck_to_h0(img_embed_to_bottleneck(img_embeds))

        # Embed all tokens but the last for LSTM input,
        # remember that Embedding is callable,
        # use `sentences` placeholder as input
        word_embeds = word_embed(sentences[:, :-1])

        # During training we use ground truth tokens (`word_embeds`) as context
        # for next token prediction. That means we know all the inputs for
        # our LSTM and can get all the hidden states with one TensorFlow
        # operation (`tf.nn.dynamic_rnn`).
        # `hidden_states` has a shape of [batch_size, time steps, lstm_units]
        hidden_states, _ = tf.nn.dynamic_rnn(lstm, word_embeds,
                                             initial_state=tf.nn.rnn_cell.LSTMStateTuple(c0, h0))

        # Now we need to calculate token logits for all the hidden states.

        # First, we reshape `hidden_states` to [-1, lstm_units].
        flat_hidden_states = tf.reshape(hidden_states, [-1, lstm_units])

        # Then, we calculate logits for next tokens using `token_logits_bottleneck`
        # and `token_logits` layers.
        flat_token_logits = token_logits(token_logits_bottleneck(flat_hidden_states))

        # Then, we flatten the ground truth token ids. Remember that we predict 
        # next tokens for each time step. Use `sentences` placeholder.
        flat_ground_truth = tf.reshape(sentences[:, 1:], [-1])

        # We need to know where we have real tokens (not padding) in `flat_ground_truth`.
        # We don't want to propagate the loss for padded output tokens. Fill 
        # `flat_loss_mask` with 1.0 for real tokens (not `pad_idx`) and 0.0 otherwise.
        flat_loss_mask = tf.cast(tf.not_equal(flat_ground_truth, pad_idx), dtype=tf.float32)

        # Compute cross-entropy between `flat_ground_truth` and `flat_token_logits` 
        # predicted by the LSTM.
        xent = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=flat_ground_truth,
                                                              logits=flat_token_logits)

        # Compute average `xent` over tokens with nonzero `flat_loss_mask`.
        # We don't want to account misclassification of PAD tokens -
        # PAD tokens are for batching purposes only!
        loss = tf.reduce_sum(xent * flat_loss_mask) / tf.reduce_sum(flat_loss_mask)
    '''

    # define optimizer operation to minimize the loss
    optimizer = tf.train.AdamOptimizer(learning_rate=constants['learning_rate'])
    train_step = optimizer.minimize(decoder.loss)

    # will be used to save/load network weights.
    # you need to reset your default graph and define it in the same way to be able to load the saved weights!
    saver = tf.train.Saver()

    if constants['retrain'] or not os.path.exists('weights.index'):
        train(sess, train_step, decoder, data, constants, saver, reproducible=True)

        # save graph weights to file!
        saver.save(sess, os.path.abspath('weights'))

    # else:
        # you can load trained weights here
        # you can load "weights_{epoch}" and continue training
        # uncomment the next line if you need to load weights
        # saver.restore(sess, os.path.abspath('weights'))

    writer.close()

    check_after_training(3, decoder, data, constants)

    model = model_builder(sess, constants, decoder, saver)

    show = partial(show_valid_example, sess, model, data, constants)

    show(example_idx=100)

    # sample more images from validation
    zf = ZipFile(DATA_DIR + 'image_captioning/val2014_sample.zip')
    for idx in np.random.choice(range(len(zf.filelist) - 1), 10):
        show(example_idx=idx)
        time.sleep(1)


if __name__ == "__main__":
    # read args
    parser = ArgumentParser(description='Run Keras Image Captioning')
    parser.add_argument('--epochs', dest='n_epochs', type=int, help='number epochs')
    parser.add_argument('--learning_rate', dest='learning_rate', type=float, help='learning rate')
    parser.add_argument('--img_size', dest='img_size', type=int, help='image size')
    parser.add_argument('--model-filename', dest='model_filename', help='model filename')
    parser.add_argument('--retrain', dest='retrain', help='retrain flag', action='store_true')
    parser.set_defaults(retrain=False)
    args = parser.parse_args()
    run(vars(args))
