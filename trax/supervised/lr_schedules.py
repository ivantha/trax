# coding=utf-8
# Copyright 2020 The Trax Authors.
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

# Lint as: python3
"""Trax learning rate schedules.

The learning rate schedules here all have the signature:
  lr: history -> (step -> {'learning_rate': lr})

That is, they are functions that take a trax.history.History and return a
function that takes a step and returns a dict with entry 'learning_rate'.
"""

import gin

from trax.fastmath import numpy as jnp
from trax.supervised import lr_functions


@gin.configurable(blacklist=['history'])
def constant(history, value):
  """Returns an LR schedule that is constant from time (step) 1 to infinity."""
  del history
  return _from_lr_function(lr_functions.constant, value)


@gin.configurable(blacklist=['history'])
def warmup(history, n_warmup_steps, max_value):
  """Returns an LR schedule with linear warm-up followed by constant value.

  Args:
    history: training history (unused in this schedule)
    n_warmup_steps: Number of steps during which the learning rate rises on
        a line connecting (0, 0) and (n_warmup_steps, max_value).
    max_value: Value for learning rate after warm-up has finished.
  """
  del history
  return _from_lr_function(lr_functions.warmup, n_warmup_steps, max_value)


@gin.configurable(blacklist=['history'])
def warmup_and_rsqrt_decay(history, n_warmup_steps, max_value):
  """Returns an LR schedule with warm-up + reciprocal square root decay."""
  del history
  return _from_lr_function(lr_functions.warmup_and_rsqrt_decay,
                           n_warmup_steps, max_value)


# We use a mix of CamelCase and not in this module.
# pylint: disable=invalid-name


@gin.configurable(blacklist=['history'])
def MultifactorSchedule(history=None,
                        factors='constant * linear_warmup * rsqrt_decay',
                        constant=0.1,  # pylint: disable=redefined-outer-name
                        warmup_steps=400,
                        decay_factor=0.5,
                        steps_per_decay=20000,
                        steps_per_cycle=100000):
  """Factor-based learning rate schedule.

  Interprets factors in the factors string which can consist of:
  * constant: interpreted as the constant value,
  * linear_warmup: interpreted as linear warmup until warmup_steps,
  * rsqrt_decay: divide by square root of max(step, warmup_steps)
  * decay_every: Every k steps decay the learning rate by decay_factor.
  * cosine_deay: Cyclic cosine decay, uses steps_per_cycle parameter.

  Args:
    history: the history of training and evaluation (History object).
    factors: a string with factors separated by '*' that defines the schedule.
    constant: float, the starting constant for the learning rate schedule.
    warmup_steps: how many steps to warm up for in the warmup schedule.
    decay_factor: The amount to decay the learning rate by.
    steps_per_decay: How often to decay the learning rate.
    steps_per_cycle: Steps per cycle when using cosine decay.

  Returns:
    a function learning_rate(step): float -> {'learning_rate': float}, the
    step-dependent lr.
  """
  del history

  factors = [n.strip() for n in factors.split('*')]

  def learning_rate(step):
    """Step to learning rate function."""
    ret = 1.0
    for name in factors:
      if name == 'constant':
        ret *= constant
      elif name == 'linear_warmup':
        ret *= jnp.minimum(1.0, step / warmup_steps)
      elif name == 'rsqrt_decay':
        ret /= jnp.sqrt(jnp.maximum(step, warmup_steps))
      elif name == 'rsqrt_normalized_decay':
        ret *= jnp.sqrt(warmup_steps)
        ret /= jnp.sqrt(jnp.maximum(step, warmup_steps))
      elif name == 'decay_every':
        ret *= (decay_factor ** (step//steps_per_decay))
      elif name == 'cosine_decay':
        progress = jnp.maximum(
            0.0, (step - warmup_steps) / float(steps_per_cycle))
        ret *= (0.5 * (1.0 + jnp.cos(jnp.pi * (progress % 1.0))))
      else:
        raise ValueError('Unknown factor %s.' % name)
    ret = jnp.asarray(ret, dtype=jnp.float32)
    return {'learning_rate': ret}

  return learning_rate


@gin.configurable(blacklist=['history'])
def EvalAdjustingSchedule(history,
                          constant=0.1,  # pylint: disable=redefined-outer-name
                          steps_to_decrease=20,
                          improvement_margin=0.001,
                          decrease_rate=1.5,
                          history_mode='eval',
                          metric='metrics/accuracy'):
  """Learning rate that decreases when eval metric stalls.

  If the chosen metric does not improve by improvement_margin for as many as
  steps_to_decrease steps, then the constant gets decreased by decrease rate.
  Finally, the MultifactorSchedule gets called with the adjusted constant.

  Args:
    history: trax.history.History, the history of training and evaluation.
    constant: float, the starting constant for the learning rate schedule.
    steps_to_decrease: int, after how many steps without improvement
      should we decrease the constant.
    improvement_margin: how much we need to improve to consider the metric
      improved.
    decrease_rate: by what fraction to decrease (i.e. lr /= decrease_rate).
    history_mode: str, which mode of the history to use.
    metric: which evaluation metric to use for adjustments.

  Returns:
    a function learning_rate(step): float -> {'learning_rate': float}, the
    step-dependent lr.
  """
  metrics = history.get(history_mode, metric)
  adjusted = constant
  if len(metrics) < 2:
    return MultifactorSchedule(history, constant=adjusted)

  steps_without_improvement = 0
  cur = metrics.pop()[1]  # The most-recent value of the metric.
  while len(metrics) > 1:
    # The one-before value of metrics as .pop() removes one element each time.
    prev = metrics.pop()[1]
    if cur < prev * (1 + improvement_margin):
      steps_without_improvement += 1
    else:
      cur = prev
      steps_without_improvement = 0
    if steps_without_improvement >= steps_to_decrease:
      adjusted /= decrease_rate
      cur = prev
      steps_without_improvement = 0

  return MultifactorSchedule(history, constant=adjusted)


def _from_lr_function(lr_fn, *args):
  """Compatibility layer: creates a learning rate from lr_functions function."""
  def learning_rate(step):
    return {'learning_rate': lr_fn(*args)(step)}
  return learning_rate
