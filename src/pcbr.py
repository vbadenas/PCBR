import os, sys, logging
from collections.abc import Callable
from typing import Union
import numpy as np
from datetime import datetime

sys.path.append(os.path.dirname(__file__))

from data.preprocessor import read_initial_cbl, read_table
import pandas as pd
from data.mapper import Mapper
from utils.io import read_file
from utils.typing import represents_int, str_to_dict
from neighbors.knn import KNeighborsClassifier
from neighbors.nn import NearestNeighbors as OurNearestNeighbors
from adapt_pc import AdaptPC
from user_request import UserRequest
from matplotlib import pyplot as plt

# Logger objects
pcbr_logger = logging.getLogger('pcbr')
retrieve_logger = logging.getLogger('retrieve')
reuse_logger = logging.getLogger('reuse')
revise_logger = logging.getLogger('revise')
retain_logger = logging.getLogger('retain')


def setup_logging():
    # Set up logging subsystem. Level should be one of: CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET
    logging.basicConfig()

    # Choose the modules whose output you want to see here. INFO is a good default level, but DEBUG
    # may be useful during development of your module
    pcbr_logger.setLevel(logging.INFO)
    retrieve_logger.setLevel(logging.INFO)
    reuse_logger.setLevel(logging.INFO)
    revise_logger.setLevel(logging.INFO)
    retain_logger.setLevel(logging.INFO)


class PCBR:
    def __init__(self, cbl_path='../data/pc_specs.csv',
                       cpu_path='../data/cpu_table.csv',
                       gpu_path='../data/gpu_table.csv',
                       ram_path='../data/ram_table.csv',
                       ssd_path='../data/ssd_table.csv',
                       hdd_path='../data/hdd_table.csv',
                       opt_drive_path='../data/optical_drive_table.csv',
                       feature_scalers_meta='../data/feature_scalers.json',
                       feature_relevance_path='../data/feature_relevance.csv'):

        pcbr_logger.info('Initializing...')
        # read mappers
        # read case library
        case_library, self.transformations = read_initial_cbl(path=cbl_path, 
            cpu_path=cpu_path,
            gpu_path=gpu_path,
            ram_path=ram_path,
            ssd_path=ssd_path,
            hdd_path=hdd_path,
            opt_drive_path=opt_drive_path,
            feature_scalers_meta=feature_scalers_meta
        )

        # Split into "source" (preferences) and "target" (PC specs)
        self.target_attributes = case_library[case_library.columns[:7]]
        self.source_attributes = case_library[case_library.columns[7:]]

        # read component's tables
        cpu_mapper = Mapper.from_csv(path=cpu_path, scaler_columns=['CPU Mark'],
                                     scaler=self.transformations['CPU'])
        gpu_mapper = Mapper.from_csv(path=gpu_path, scaler_columns=['Benchmark'],
                                     scaler=self.transformations['GPU'])
        ram_mapper = Mapper.from_csv(path=ram_path, scaler_columns=['Capacity'],
                                     scaler=self.transformations['RAM (GB)'])
        ssd_mapper = Mapper.from_csv(path=ssd_path, scaler_columns=['Capacity'],
                                     scaler=self.transformations['SSD (GB)'])
        hdd_mapper = Mapper.from_csv(path=hdd_path, scaler_columns=['Capacity'],
                                     scaler=self.transformations['HDD (GB)'])
        opt_drive_mapper = Mapper.from_csv(path=opt_drive_path, scaler_columns=['Boolean State'],
                                           scaler=self.transformations['Optical Drive (1 = DVD; 0 = None)'])
        # sorted in the order of the case_library
        self.mappers = [cpu_mapper, ram_mapper, ssd_mapper, hdd_mapper, gpu_mapper, opt_drive_mapper]

        # feature relevance matrix for UserRequests
        self.feature_relevance_matrix = np.loadtxt(feature_relevance_path, delimiter=',', ndmin=2)

        # initialize the adapt_pc object
        self.adapt_pc = AdaptPC(self)
        date_time = datetime.now()
        self.run_timestamp = date_time.strftime("%Y-%m-%d-%H-%M-%S")

        pcbr_logger.info('Initialization complete!')

    def get_user_request(self, mock_file=None, mode='one_pass') -> UserRequest:

        # Request input here and return it.
        # For now, appears "None" is handled well by retrieve step and it defaults to a case in the library
        # Either need to pre-process the request here or in the retrieve step.
        # Also need to pass along some extra metadata, such as constraints.
        if mock_file is not None:

            # initialize the data and the iteration index pointing to the 
            # instance to be returned in this call
            if not hasattr(self, 'mock_user_requests'):
                self.mock_user_requests = read_file(mock_file, sep='\t')
                self.mock_requests_idx = 0

            # end of loop control.
            if self.mock_requests_idx >= len(self.mock_user_requests):
                if mode == 'one_pass':
                    # if we reached the end of the mock list, 
                    # return None as no more instances are available.
                    return None
                elif mode == 'cyclic':
                    # if we reach the end of the mock list and we are in cyclic,
                    # reset the index and start again
                    self.mock_requests_idx = self.mock_requests_idx % len(self.mock_user_requests)

            # load current iteration mock request
            request_strings = self.mock_user_requests[self.mock_requests_idx]
            
            # increment pointer
            self.mock_requests_idx += 1

            # return request built with mock request trings
            return UserRequest(*request_strings, self.transformations,self.feature_relevance_matrix)
        else:
            # TODO: CLI request
            profile_str, pref_str, constraints_str = self.get_cli_requests()
            if profile_str is None or pref_str is None or constraints_str is None:
                return None
            user_req_rv = UserRequest(
                profile_str,
                pref_str,
                constraints_str,
                self.transformations,
                self.feature_relevance_matrix
            )
            return user_req_rv

    def get_cli_requests(self):
        profile_str = self.get_user_input(
            'input profile (12 comma separated values i.e. 2, 1, Programming, 1, 3, 1, 0, 0, 0, 1, 0, 0):\n',
            self.profile_str_valid
        )
        if profile_str is None:
            return None, None, None

        pref_str = self.get_user_input(
            'input preferences (13 comma separated values i.e. 5, 2, 3, 1, 2, 1, 3, 4, 1, 0, 1, 0, 0):\n',
            self.preference_str_valid
        )
        if pref_str is None:
            return None, None, None

        constraints_str = self.get_user_input(
            'input constraints (key:value pairs i.e. cpu_brand: PreferIntel, gpu_brand: AMD, min_ram: 32, max_budget: 1500):\n',
            self.constraints_str_valid
        )
        if constraints_str is None:
            return None, None, None

        return profile_str, pref_str, constraints_str

    @staticmethod
    def get_user_input(input_message_string:str, expected_format:Callable, exit_str:str='exit') -> Union[str, None]:
        user_input_string = input(input_message_string).strip()
        if user_input_string == exit_str:
            return None
        while not expected_format(user_input_string):
            print('Wrong format...')
            user_input_string = input(input_message_string).strip()
            if user_input_string == exit_str:
                return None
        return user_input_string

    @staticmethod
    def profile_str_valid(string:str):
        split_str = string.split(',')
        if len(split_str) != 12:
            return False
        int_check = list(map(represents_int, split_str))
        int_check[2] = not int_check[2]
        return all(int_check)

    @staticmethod
    def preference_str_valid(string:str):
        split_str = string.split(',')
        if len(split_str) != 13:
            return False
        int_check = tuple(map(represents_int, split_str))
        return all(int_check)

    @staticmethod
    def constraints_str_valid(string:str):
        try:
            str_to_dict(string)
            return True
        except Exception:
            return False

    def retrieve(self, new_instance=None, feature_weights=None, n_neighbors=2):
        if new_instance is None:
            new_instance = self.source_attributes.iloc[2].to_numpy().reshape(1, -1)
        if feature_weights is None:
            feature_weights = 'uniform'
        pcbr_logger.debug('looking for: ' + str(new_instance))
        clf = KNeighborsClassifier(n_neighbors=n_neighbors, weights=feature_weights).fit(
            self.source_attributes.to_numpy(),
            self.target_attributes.to_numpy()
        )
        return clf.predict(new_instance)

    def reuse(self, nearest_cases=None, distances=None, user_request=None):
        assert (nearest_cases is not None)
        pcbr_logger.debug('starting with: ' + str(nearest_cases))
        adapted_case = self.adapt_pc.adapt(nearest_cases, distances, self.mappers,
                                           [self.transformations['RAM (GB)']['scaler'],
                                            self.transformations['SSD (GB)']['scaler'],
                                            self.transformations['HDD (GB)']['scaler'],
                                            self.transformations['Price (€)']['scaler']],
                                           user_request)
        pcbr_logger.debug('adapted to: ' + str(adapted_case))
        return adapted_case

    def revise(self, proposed_solution=None):
        assert proposed_solution is not None

        print('\n***************************************\n')
        print('\t\t\tEXPERT OPINION')
        print('***************************************')

        proposed_solutions = [proposed_solution]
        index = ['Proposed solution']
        columns = self.target_attributes.columns.tolist()
        self.print_solutions(proposed_solutions, columns, index)
        satisfactory = self.ask_if('Is the latter proposed solution satisfactory (y/n)?')
        if not satisfactory:
            revise_result = self.revise_possibilities(proposed_solutions, columns)
            if revise_result is not None:
                print('***************************************')
                print('\t\tEXPERT OPINION END\n')
                index = ['Final revised solution']
                self.print_solutions([revise_result], columns, index, print_pre_message=False)
                print('***************************************')
        else:
            revise_result = proposed_solution
            print('***************************************\n')
            print('\t\tEXPERT OPINION END\n')
            print('The proposed solution has been confirmed!')
            print('***************************************')
        return revise_result

    def print_solutions(self, proposed_solutions, columns, index, print_pre_message=True):
        dataframe = pd.DataFrame(proposed_solutions, columns=columns, index=index)
        pd.set_option('max_columns', None)
        pd.set_option('display.expand_frame_repr', False)
        if print_pre_message:
            print('\n---------------------------------------')
            if len(index) == 1:
                print('The proposed solution is the following:\n')
            else:
                print('The modified solutions are the following:\n')
        print(dataframe.to_markdown(), '\n')

    def ask_if(self, binary_question):
        while True:
            print(binary_question)
            cli_input = input()
            if cli_input is not None:
                if cli_input.lower() == 'y' or cli_input.lower() == 'yes':
                    return True
                elif cli_input.lower() == 'n' or cli_input.lower() == 'no':
                    return False
                else:
                    print(f'Invalid choice: {cli_input}')
            else:
                print(f'Invalid choice: {cli_input}')

    def revise_possibilities(self, proposed_solutions, components):
        want_to_modify = self.ask_if('Would you like to change any components (y/n)? (n will drop the solution)')
        if want_to_modify:
            remaining_components = components[:-1]
            index = ['Original solution']
            satisfactory = False
            while len(remaining_components) > 0 and not satisfactory:
                print('Select one component number between the following ones:')
                for idx, component in enumerate(remaining_components):
                    print(f'{idx} - {component}')
                while True:
                    cli_input = input()
                    if cli_input is not None and (cli_input.isdigit() and 0 <= int(cli_input) < len(remaining_components)):
                        selected_component_idx = int(cli_input)
                        selected_component = remaining_components[selected_component_idx]
                        remaining_components.pop(selected_component_idx)
                        all_values = self.extract_all_values_for_component(selected_component)
                        latest_solution = proposed_solutions[len(proposed_solutions) - 1]
                        latest_solution_price = proposed_solutions[len(proposed_solutions) - 1][-1]
                        component_id = components.index(selected_component)
                        latest_value = latest_solution[component_id]
                        all_values.remove(latest_value)
                        if len(all_values) > 0:
                            selected_new_value = self.show_values_and_get_choice(selected_component, all_values, latest_value)
                            difference = self.calculate_price_difference(selected_component, latest_value, selected_new_value)
                            latest_solution_price += difference
                            new_solution = latest_solution.copy()
                            new_solution[component_id] = selected_new_value
                            new_solution[-1] = latest_solution_price
                            proposed_solutions.append(new_solution)
                            break
                        else:
                            print('Sorry but the chosen component has not valid alternatives!')
                            print('\nSelect one component number between the following ones:')
                    else:
                        print(f'Invalid choice: {cli_input}')

                index.append(len(index))
                self.print_solutions(proposed_solutions, components, index)
                if len(remaining_components) > 0:
                    satisfactory = not self.ask_if('Would you like to change something more (y/n)?')
            return self.ask_which_solution_is_final(proposed_solutions, index)
        else:
            print('***************************************\n')
            print('\t\tEXPERT OPINION END\n')
            print('The proposed solution has been dropped!')
            print('***************************************')
            return None

    def extract_all_values_for_component(self, selected_component):
        components_map = {'CPU': ('../data/cpu_table.csv', 'CPU Name'),
                 'RAM (GB)': ('../data/ram_table.csv', 'Capacity'),
                 'SSD (GB)': ('../data/ssd_table.csv', 'Capacity'),
                 'HDD (GB)': ('../data/hdd_table.csv', 'Capacity'),
                 'GPU': ('../data/gpu_table.csv', 'GPU Name'),
                 'Optical Drive (1 = DVD; 0 = None)': ('../data/optical_drive_table.csv', 'Boolean State')}

        df = read_table(components_map[selected_component][0], index_col=None)
        return df[components_map[selected_component][1]].values.tolist()

    def show_values_and_get_choice(self, selected_component, all_values, latest_value):
        self.print_all_values(selected_component, all_values)
        while True:
            print(f'What component would you like to use instead of "{latest_value}"?')
            cli_input = input()
            if cli_input is not None and cli_input.isdigit():
                if 0 <= int(cli_input) < len(all_values):
                    return all_values[int(cli_input)]
                else:
                    print(f'Invalid choice: {cli_input}')
            else:
                print(f'Invalid choice: {cli_input}')

    def calculate_price_difference(self, selected_component, latest_value, selected_new_value):
        components_map = {'CPU': ('../data/cpu_table.csv', 'CPU Name', 'MSRP'),
                 'RAM (GB)': ('../data/ram_table.csv', 'Capacity', 'Price'),
                 'SSD (GB)': ('../data/ssd_table.csv', 'Capacity', 'Price'),
                 'HDD (GB)': ('../data/hdd_table.csv', 'Capacity', 'Price'),
                 'GPU': ('../data/gpu_table.csv', 'GPU Name', 'MSRP'),
                 'Optical Drive (1 = DVD; 0 = None)': ('../data/optical_drive_table.csv', 'Boolean State', 'Price')}
        df = read_table(components_map[selected_component][0], index_col=None)
        old_row = df.loc[df[components_map[selected_component][1]] == latest_value]
        new_row = df.loc[df[components_map[selected_component][1]] == selected_new_value]
        old_value = old_row[components_map[selected_component][2]].values[0]
        new_value = new_row[components_map[selected_component][2]].values[0]
        difference = new_value - old_value
        return difference

    def print_all_values(self, selected_component, values):
        dataframe = pd.DataFrame(values, columns=[selected_component])
        pd.set_option('max_rows', None)
        pd.set_option('display.expand_frame_repr', False)
        print('---------------------------------------')
        if values == 1:
            print('The only possible value for the component is the following:\n')
        else:
            print('All possible values for the component are the following:\n')
        print(dataframe.to_markdown(), '\n')

    def ask_which_solution_is_final(self, proposed_solutions, index):
        self.print_solutions(proposed_solutions, self.target_attributes.columns.tolist(), index)
        while True:
            print(f'Which configuration do you want to keep? (Select a configuration number)')
            cli_input = input()
            if cli_input is not None and cli_input.isdigit():
                if 0 < int(cli_input) < len(proposed_solutions):
                    return proposed_solutions[int(cli_input)]
                else:
                    print(f'Invalid choice: {cli_input}')
            else:
                print(f'Invalid choice: {cli_input}')

    def retain(self, revised_solution=None, n_neighbors=3):
        assert proposed_solution is not None and revision_result is not None

        target = self.target_attributes.to_numpy()
        numeric_revised_solution = self.adapt_pc.from_pc_to_numeric(revised_solution)

        knn = OurNearestNeighbors(n_neighbors=n_neighbors).fit(target)
        neigh = knn.kneighbors_graph(target)
        prediction = knn.kneighbors([numeric_revised_solution])

        stats = self.extract_statistics(neigh, n_neighbors)
        print('\n---------------------------------------')
        pcbr_logger.debug(f"Distance to the closest point from the prediction: {prediction[0][0][0]}")
        pcbr_logger.debug(f"STATISTICS")
        pcbr_logger.debug(stats.head(), '\n')
        if prediction[0][0][0] >= stats['85%'][0]:
            print("The proposed solution has been stored!")
            self.save_new_solution(revised_solution)
        else:
            print("The proposed solution has NOT been stored!")
        print('---------------------------------------\n')

    def extract_statistics(self, neigh, n_neighbors, plot_points=False):
        distances_map = {point: [] for point in range(1, n_neighbors)}
        for index, distance in enumerate(neigh.data):
            mode = index % n_neighbors
            if mode != 0:
                distances_map[mode].append(distance)
        descriptions = []
        for dist in distances_map.values():
            df = pd.DataFrame(dist)
            desc = df.describe(percentiles=[.25, .5, .75, .80, .85, .90, .95]).T
            descriptions.append(desc)
        statistics = pd.concat(descriptions, axis=0)
        stats = pd.DataFrame(statistics.values, columns=statistics.columns)

        # TODO to plot the distances between the nn of every instance in the dataset
        if plot_points:
            plt.scatter(distances_map[1], [0]*len(distances_map[1]), alpha=0.5, cmap='reds', s=100)
            plt.show()
        return stats

    def save_new_solution(self, revised_solution):
        path = f"../data/retained/pcbr_{self.run_timestamp}.csv"
        columns = self.target_attributes.columns.tolist()
        solution = pd.DataFrame([revised_solution], columns=columns, index=None)
        if os.path.isfile(path):
            retained = pd.read_csv(path, index_col=None)
            retained = retained.append(solution, ignore_index=True)
        else:
            retained = solution

        retained.to_csv(path, index=False)


if __name__ == '__main__':
    import time
    setup_logging()

    # initialize pcbr
    pcbr = PCBR()

    while True:
        # starting time
        st = time.time()

        # user_request = pcbr.get_user_request() # cli
        user_request = pcbr.get_user_request(mock_file='../data/mock_requests.tsv', mode='one_pass') # mock_file

        if not isinstance(user_request, UserRequest):
            # if get_user_request returns None, the mock file lines have been exhausted, stop run
            break

        # user_request is a UserRequest object, keep moving forward.
        n_neighbors = 3
        nearest_cases, distances = pcbr.retrieve(new_instance=user_request.profile,
                                                 feature_weights=user_request.preferences, n_neighbors=n_neighbors)
        pcbr_logger.debug(nearest_cases)
        pcbr_logger.debug(nearest_cases.shape)

        proposed_solution = pcbr.reuse(nearest_cases=nearest_cases[0], distances=distances, user_request=user_request)

        proc_time = time.time()
        revision_result = pcbr.revise(proposed_solution)
        if revision_result is not None:  # If the expert has not dropped the solution
            pcbr.retain(revision_result, n_neighbors=n_neighbors)

        rev_ret_time = time.time()

        # compute ending time and print it, move onto next item
        pcbr_logger.info(f'time for processing an instance {proc_time - st:.2f}s, time for revision and {rev_ret_time - st:.2f}s')

    # Kevin: I think that we talked yesterday about just keeping the loop.
