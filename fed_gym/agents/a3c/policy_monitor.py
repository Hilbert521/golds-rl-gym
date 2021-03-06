import os
import json
import time

import numpy as np
import tensorflow as tf
from gym.wrappers import Monitor

from .estimators import GaussianPolicyEstimator, rnn_graph_lstm
from .worker import make_copy_params_op


class PolicyMonitor(object):
    """
    Helps evaluating a policy by running an episode in an environment,
    saving a video, and plotting summaries to Tensorboard.

    Args:
      env: environment to run in
      policy_net: A policy estimator
      summary_writer: a tf.train.SummaryWriter used to write Tensorboard summaries
    """
    def __init__(self, env, global_policy_net, state_processor, summary_writer, saver=None, num_actions=None, input_size=None, temporal_size=None):

        self.env = Monitor(env, directory=os.path.abspath(summary_writer.get_logdir()), resume=True)
        self.state_processor = state_processor
        self.global_policy_net = global_policy_net
        self.summary_writer = summary_writer
        self.saver = saver

        self.checkpoint_path = os.path.abspath(os.path.join(summary_writer.get_logdir(), "../checkpoints/model"))

        # Local policy net
        with tf.variable_scope("policy_eval"):
            self.policy_net = self._create_policy_estimator(num_actions, input_size, temporal_size)

        # Op to copy params from global policy/value net parameters
        self.copy_params_op = make_copy_params_op(
            tf.contrib.slim.get_variables(scope="global", collection=tf.GraphKeys.TRAINABLE_VARIABLES),
            tf.contrib.slim.get_variables(scope="policy_eval", collection=tf.GraphKeys.TRAINABLE_VARIABLES))

    def get_sigmoid_action_from_mu(self, processed_state, history, sess):
        mu = self.policy_net.predict(processed_state, history, sess)['mu'].flatten()[0]
        return 1. / (1 + np.exp(- mu))

    def get_action_from_policy(self, processed_state, history, sess):
        return self.get_sigmoid_action_from_mu(processed_state, history, sess)

    @staticmethod
    def _create_policy_estimator(num_actions, input_size, temporal_size):
        return GaussianPolicyEstimator(
            num_actions, static_size=input_size, temporal_size=temporal_size,
            shared_layer=lambda x_t, x: rnn_graph_lstm(x_t, x, 32, 1, True),
        )

    def eval_once(self, sess, max_sequence_length=5):
        with sess.as_default(), sess.graph.as_default():
            # Copy params to local model
            global_step, _ = sess.run([tf.train.get_global_step(), self.copy_params_op])

            # Run an episode
            done = False
            state = self.env.reset()
            processed_state = self.state_processor.process_state(state)
            history = self.state_processor.process_temporal_states([processed_state])
            total_reward = 0.0
            episode_length = 0
            rewards = []
            while not done:
                action = self.get_action_from_policy(processed_state, history, sess)
                next_state, reward, done, _ = self.env.step(action)
                next_processed_state = self.state_processor.process_state(next_state)
                new_temporal_state = self.state_processor.process_temporal_states([next_processed_state])
                history = np.vstack([history, new_temporal_state])[-max_sequence_length:, :]
                total_reward += reward
                episode_length += 1
                processed_state = next_processed_state
                rewards.append(reward)

            # Add summaries
            episode_summary = tf.Summary()
            episode_summary.value.add(simple_value=total_reward, tag="eval/total_reward")
            episode_summary.value.add(simple_value=episode_length, tag="eval/episode_length")
            self.summary_writer.add_summary(episode_summary, global_step)
            self.summary_writer.flush()

            # if self.saver is not None:
            #     self.saver.save(sess, self.checkpoint_path)

            tf.logging.info(
                "Eval results at step {}: avg_reward {}, std_reward {}, episode_length {}".format(
                    global_step, np.mean(rewards), np.std(rewards), episode_length
                )
            )

            return total_reward, episode_length, rewards

    def continuous_eval(self, eval_every, sess, coord, worker, max_seq_length, total_reward_log_file=None):
        """
        Continuously evaluates the policy every [eval_every] seconds.
        """
        total_rewards = []
        episode_lengths = []
        try:
            while not coord.should_stop():
                total_reward, episode_length, rewards = self.eval_once(sess, max_sequence_length=max_seq_length)
                total_rewards.append(total_reward)
                episode_lengths.append(episode_length)
                # Sleep until next evaluation cycle
                if total_reward_log_file:
                    with open(total_reward_log_file, 'w') as f:
                        json.dump(
                            {
                                'total_reward': total_rewards,
                                'episode_length': episode_lengths,
                            },
                            f
                        )
                time.sleep(eval_every)
        except tf.errors.CancelledError:
            return
