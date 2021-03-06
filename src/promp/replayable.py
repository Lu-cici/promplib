from .interactive import InteractiveProMP
from os import makedirs
from os.path import exists, join, isfile
import json

class ReplayableInteractiveProMP(InteractiveProMP):
    """
    Interactive ProMP that also stores the sequence of demos and goal requests for comparison
    """
    def __init__(self, arm, epsilon_ok=0.03, with_orientation=True, min_num_demos=3, std_factor=2, path_ds='.', dataset_id=-1, path_plots='/tmp/plots'):
        """
        :param arm: string ID of the FK/IK group (left, right, ...)
        :param epsilon_ok: maximum acceptable cartesian distance to the goal
        :param with_orientation: True for context = position + orientation, False for context = position only
        :param min_num_demos: Minimum number of demos per primitive
        :param std_factor: Factor applied to the cartesian standard deviation so within this range, the MP is valid
        :param path_ds: path to the datasets folder
        :param dataset_id: ID of the dataset to work with, id < 0 will create a new one
        :param path_plots: path to the plots folder ('' to disable)
        """
        self.id = dataset_id

        def get_dataset_path(dataset_id):
            return join(path_ds, 'dataset_{}'.format(dataset_id))

        def generate_next_dataset():
            for id in range(100):
                path = get_dataset_path(id)
                if not exists(path):
                    self.id = id
                    return path

        self.dataset_path = generate_next_dataset() if dataset_id < 0 else get_dataset_path(dataset_id)
        if not exists(self.dataset_path):
            makedirs(self.dataset_path)

        self.record_demo_id = 0
        self.record_goal_id = 0
        self.sequence = []
        super(ReplayableInteractiveProMP, self).__init__(arm, epsilon_ok, with_orientation, min_num_demos, std_factor,
                                                         join(path_plots, 'dataset_{}'.format(self.id)))

    def add_demonstration(self, demonstration, eef_demonstration):
        """
        Add a new  demonstration for this skill and stores it into the current data set
        Automatically determine whether it is added to an existing a new ProMP
        :param demonstration: Joint-space demonstration demonstration[time][joint]
        :param eef_demonstration: Full end effector demo [[[x, y, z], [qx, qy, qz, qw]], [[x, y, z], [qx, qy, qz, qw]]...]
        :return: The ProMP id that received the demo
        """
        demonstration_file = join(self.dataset_path, 'demo_{}.json'.format(self.record_demo_id))
        eef_demonstration_file = join(self.dataset_path, 'path_{}.json'.format(self.record_demo_id))
        with open(demonstration_file, 'w') as f:
            json.dump(demonstration, f)
        with open(eef_demonstration_file, 'w') as f:
            json.dump(eef_demonstration, f)

        promp_index = super(ReplayableInteractiveProMP, self).add_demonstration(demonstration, eef_demonstration)
        self.sequence.append({'type': 'demo', 'added_to': promp_index})
        self.record_demo_id += 1
        return promp_index

    def set_goal(self, x_des, joint_des=None):
        """
        Set a new task-space goal, and determine which primitive will be used
        :param x_des: desired task-space goal
        :param joint_des desired joint-space goal **ONLY used for plots**
        :return: True if the goal has been taken into account, False if a new demo is needed to reach it
        """
        ret = super(ReplayableInteractiveProMP, self).set_goal(x_des, joint_des)
        cartesian_goal_file = join(self.dataset_path, 'cart_goal_{}.json'.format(self.record_goal_id))
        joint_goal_file = join(self.dataset_path, 'joint_goal_{}.json'.format(self.record_goal_id))
        with open(cartesian_goal_file, 'w') as f:
            json.dump(x_des, f)
        if joint_des is not None:
            with open(joint_goal_file, 'w') as f:
                json.dump(joint_des, f)
        self.sequence.append({'type': 'goal', 'id': self.goal_id, 'log': self.goal_log})
        self.record_goal_id += 1
        return ret

    def close(self):
        sequence_file = join(self.dataset_path, 'sequence.json')
        with open(sequence_file, 'w') as f:
            json.dump(self.sequence, f)
        self.record_demo_id = 0
        self.record_goal_id = 0
        super(ReplayableInteractiveProMP, self).clear()

    def _play_next_demo(self, receiving_promp):
        demonstration_file = join(self.dataset_path, 'demo_{}.json'.format(self.record_demo_id))
        eef_demonstration_file = join(self.dataset_path, 'path_{}.json'.format(self.record_demo_id))
        with open(demonstration_file) as f:
            demonstration = json.load(f)
        with open(eef_demonstration_file) as f:
            eef_demonstration = json.load(f)
        self.record_demo_id += 1
        promp_target = -1 if self.num_primitives <= receiving_promp else receiving_promp
        return super(ReplayableInteractiveProMP, self).add_demonstration(demonstration, eef_demonstration,
                                                                         force_mp_target=promp_target)

    def _play_next_goal(self, refining=True):
        cartesian_goal_file = join(self.dataset_path, 'cart_goal_{}.json'.format(self.record_goal_id))
        joint_goal_file = join(self.dataset_path, 'joint_goal_{}.json'.format(self.record_goal_id))
        with open(cartesian_goal_file) as f:
            x_des = json.load(f)
        if isfile(joint_goal_file):
            with open(joint_goal_file) as f:
                joint_des = json.load(f)
        else:
            joint_des = None
        self.record_goal_id += 1
        result = super(ReplayableInteractiveProMP, self).set_goal(x_des, joint_des, refining)
        trajectory = super(ReplayableInteractiveProMP, self).generate_trajectory() if result else None
        return result, trajectory

    def play(self, keep_targets=True, refining=True):
        """
        Replay this sequence
        :param keep_targets: True to replay the sequence by respecting the same MP targets, False to recompute the best target
        :param refining: True to enable post-process refining, False to disable
        :return: the timeline of results of these goal or demo events
        """
        self.record_demo_id = 0
        self.record_goal_id = 0

        sequence_file = join(self.dataset_path, 'sequence.json')
        with open(sequence_file) as f:
            self.sequence = json.load(f)

        timeline = []
        for event in self.sequence:
            if event['type'] == 'demo':
                target = self._play_next_demo(event['added_to'] if keep_targets else -1)
                timeline.append({'type': 'demo', 'added_to': target})
            elif event['type'] == 'goal':
                is_reached, trajectory = self._play_next_goal(refining)
                timeline.append({'type': 'goal', 'is_reached': is_reached, 'log': self.goal_log, 'trajectory': trajectory})
        return timeline