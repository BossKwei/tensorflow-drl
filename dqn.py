import time
import numpy as np
import tensorflow as tf
from tqdm import tqdm
import base
import enviroment
import history
import layers


class DQN(base.AgentBase):
    # Modeling
    ENV_ID = 'CartPole-v0'
    STATE_SPACE = 4
    HIDDEN_NEURONS = 20
    ACTION_SPACE = 2
    # Training
    LR = 1e-3  # learning rate
    DF = 0.9

    def __init__(self, double_q=False, dueling=False):
        super().__init__()
        # params
        self._double_q = double_q
        self._dueling = dueling
        # sub module
        self._env = enviroment.make(self.ENV_ID)
        self._replay_buffer = history.ReplayBuffer()
        # build network
        self._sess = tf.Session()
        self._net, self._target_net = self._build_network()
        # update target network
        self._epsilon_to_update = 5

    def __del__(self):
        self._sess.close()

    def _build_network(self):
        # target_xxx means tensors in target Q net, for example: tf.assign(target_w, w)
        # xxx_target means training value, for example: loss = q_target - q_current
        weights = {}
        target_weights = {}
        with tf.name_scope('input'):
            state = tf.placeholder(dtype=tf.float32, shape=[None, self.STATE_SPACE])
        with tf.name_scope('Q_Net'):
            with tf.name_scope('hidden'):
                y1, weights['W1'], weights['b1'] = layers.fc(state, n_neurons=self.HIDDEN_NEURONS,
                                                             activation=tf.nn.tanh)
            with tf.name_scope('q_value'):
                q_values, weights['W2'], weights['b2'] = layers.fc(y1, n_neurons=self.ACTION_SPACE)
        with tf.name_scope('Q_Target'):
            with tf.name_scope('hidden'):
                target_y1, target_weights['W1'], target_weights['b1'] = layers.fc(state, n_neurons=self.HIDDEN_NEURONS,
                                                                                  activation=tf.nn.tanh)
            with tf.name_scope('q_value'):
                target_q_values, target_weights['W2'], target_weights['b2'] = layers.fc(target_y1,
                                                                                        n_neurons=self.ACTION_SPACE)
            with tf.name_scope('update'):
                update_ops = []
                for name in weights:
                    update_ops.append(tf.assign(target_weights[name], weights[name]))
        # loss
        with tf.name_scope('loss'):
            action = tf.placeholder(tf.int32, [None])
            action_mask = tf.one_hot(action, depth=self.ACTION_SPACE, on_value=1.0, off_value=0.0, dtype=tf.float32)
            q_current = tf.reduce_sum(tf.multiply(q_values, action_mask), axis=1)
            q_target = tf.placeholder(tf.float32, [None])
            loss = tf.reduce_mean(tf.squared_difference(q_current, q_target))
            tf.summary.scalar('loss', loss)
        # train
        with tf.name_scope('train'):
            global_step = tf.Variable(0, trainable=False, name='global_step')
            train_step = tf.train.AdamOptimizer().minimize(loss, global_step=global_step)
        # tensor board
        merged = tf.summary.merge_all()
        train_writer = tf.summary.FileWriter('/tmp/tensorflow-drl/dqn/train', self._sess.graph)
        test_writer = tf.summary.FileWriter('/tmp/tensorflow-drl/dqn/test')
        #
        self._sess.run(tf.global_variables_initializer())
        #
        return {'state': state,
                'q_values': q_values,
                'action': action,
                'q_current': q_current,
                'q_target': q_target,
                'loss': loss,
                'train_step': train_step,
                'global_step': global_step,
                'merged': merged,
                'train_writer': train_writer,
                'test_writer': test_writer}, {'state': state,
                                              'q_values': target_q_values,
                                              'update_ops': update_ops}

    def train(self, episodes=500, max_step=200):
        for episode in tqdm(range(episodes)):
            if episode % self._epsilon_to_update == 0:
                self._update_target_network()
            #
            if episode % 50 == 0:
                total_reward = self._test_impl(max_step, delay=0, gui=False)
                tqdm.write('current reward: {total_reward}'.format(total_reward=total_reward))
            else:
                # train step
                self._train_impl(max_step)

    def test(self, episodes=1, max_step=200, delay=0.1, gui=True):
        for episode in range(episodes):
            total_reward = self._test_impl(max_step, delay, gui)
            print('current reward: {total_reward}'.format(total_reward=total_reward))

    def _update_target_network(self):
        self._sess.run(self._target_net['update_ops'])

    def _random_action(self):
        action = self._env.action_space.sample()
        return action

    def _optimal_action(self, state):
        q_values = self._sess.run(self._net['q_values'], feed_dict={self._net['state']: [state]})
        return np.argmax(q_values)

    def _perceive(self, state, action, state_, reward, done):
        # DQN2013: y_i^DQN  = r + gamma * max_a' Q(next_state, a')
        # DQN2015: y_i^DQN  = r + gamma * max_a' Q_Target(next_state, a')
        # DoubleDQN: y_i^DDQN = r + gamma * Q_Target(next_state, argmax_a' Q(next_state, a') )
        self._replay_buffer.insert([state, action, state_, reward, done])
        if self._replay_buffer.is_full():
            #
            state_batch, action_batch, next_state_batch, reward_batch, done_batch = \
                self._replay_buffer.get_batch(n_batch=128, n_lost=8)
            #
            reward_batch = [-1.0 if done else reward for reward, done in zip(reward_batch, done_batch)]
            #
            if self._double_q:
                q_predict_batch = self._sess.run(self._net['q_values'],
                                                 feed_dict={self._net['state']: next_state_batch})
                action_predict_batch = np.argmax(q_predict_batch, axis=1)
                temp_batch = self._sess.run(self._target_net['q_current'],
                                            feed_dict={self._target_net['state']: next_state_batch,
                                                       self._target_net['action']: action_predict_batch})
                q_target_batch = reward_batch + np.multiply(np.subtract(1.0, done_batch),
                                                            self.DF * temp_batch)
            else:
                q_predict_batch = self._sess.run(self._target_net['q_values'],
                                                 feed_dict={self._target_net['state']: next_state_batch})
                q_target_batch = reward_batch + np.multiply(np.subtract(1.0, done_batch),
                                                            self.DF * np.max(q_predict_batch, axis=1))
            #
            summary, _, loss = self._sess.run([self._net['merged'],
                                               self._net['train_step'],
                                               self._net['loss']],
                                              feed_dict={self._net['state']: state_batch,
                                                         self._net['action']: action_batch,
                                                         self._net['q_target']: q_target_batch})
            self._net['train_writer'].add_summary(summary,
                                                  tf.train.global_step(self._sess, self._net['global_step']))
