# System/standard imports
import logging
import numpy as np

from collections import defaultdict

# Our imports
from user_request import UserRequest

# Constants
MAP_CPU=0
MAP_RAM=1
MAP_SSD=2
MAP_HDD=3
MAP_GPU=4
MAP_OPT=5

# Module-global data
reuse_logger = logging.getLogger('reuse')

source_columns = ['CPU Mark', 'Capacity', 'Capacity', 'Capacity', 'Benchmark', 'Boolean State']
target_columns = ['CPU Name', 'Capacity', 'Capacity', 'Capacity', 'GPU Name', 'Boolean State']
price_columns = ['MSRP', 'Price', 'Price', 'Price', 'MSRP', 'Price']


# Function definitions

# Class definitions
class AdaptPC:
    """
    Class used to perform the Reuse function and adapt a case to the input request
    """

    def __init__(self, pcbr):
        """initialize the Reuse class and load domain knowledge/rules/etc.
        """
        self.pcbr = pcbr

        # Some variables to hold some things so we don't have to pass them from function
        # to function during the adaptation process. Would break thread-safety/reentrant
        # paradigm, but python's not a multi-threaded environment anyways
        # If you need to make it safe, lock at the beginning of adapt() and unlock upon exit
        self.mappers = None
        self.scalers = None
        self.user_request = None
        self.cur_symbolic_soln = None
        self.cur_numeric_soln = None
        self.cur_addl_info = None

        # Different tables with parts filtered according to constraints/preferences
        self.cpu_table = None
        self.gpu_table = None
        self.ram_table = None
        self.ssd_table = None
        self.hdd_table = None
        self.opt_drive_table = None
        # Alternate tables if preferred brand does not work
        self.cpu_table_alt = None
        self.gpu_table_alt = None

        # List of priorities based on user preference. Will be CPU, GPU, RAM, SSD, HDD, Budget in some order
        self.priorities = None

    def adapt(self, nearest_neighbors, distances, mappers, scalers, user_request):
        """start with case from case base and then apply domain knowledge to adapt it to user's needs
        """
        self.mappers = mappers
        self.scalers = scalers
        self.user_request = user_request

        # Weighted average given distances to nearest neighbors so as to create new PC.
        sims = [1/(distance + 0.1) for distance in distances]
        norm_sims = np.array([sim/np.sum(sims) for sim in sims])[0]

        adapted_solution = [np.sum([norm_sims[neighbor_idx]*nearest_neighbors[neighbor_idx][target_col]
                                    for neighbor_idx in range(nearest_neighbors.shape[0])])
                            for target_col in range(nearest_neighbors.shape[1])]

        # Use domain knowledge to adapt it
        # Kevin: Constraints will be solved after the adaptation stage. It takes into account possible compatibility
        #        issues. The solution is already optimized based on the weighted kNN.
        reuse_logger.debug('Numeric representation: ' + str(adapted_solution))
        self.cur_symbolic_soln = self._map_to_closest(adapted_solution)
        reuse_logger.debug('Configuration after weighted adaptation: ' + str(self.cur_symbolic_soln))
        additional_info=[]
        self.cur_numeric_soln = self._map_to_numeric(self.cur_symbolic_soln, additional_info=additional_info)
        self.cur_addl_info = additional_info

        reuse_logger.debug('Numeric representation (closest): ' + str(self.cur_numeric_soln))

        reuse_logger.debug('Checking constraints and optimizing...')
        reuse_logger.debug('Constraints')
        reuse_logger.debug('-----------')
        reuse_logger.debug('CPU Brand: '  + str(self.user_request.constraints.cpu_brand))
        reuse_logger.debug('GPU Brand: '  + str(self.user_request.constraints.gpu_brand))
        reuse_logger.debug('Min RAM: '    + str(self.user_request.constraints.min_ram))
        reuse_logger.debug('Max budget: ' + str(self.user_request.constraints.max_budget))
        reuse_logger.debug('Preferences: ' + str(user_request.preferences))
        reuse_logger.debug('Raw Preferences: ' + str(user_request.raw_preferences))

        # Taking a super-simple approach to constraints-checking and optimizations
        # 1. Filter out solutions that would be forbidden by the user constraints
        # 2. Filter out solutions that would be forbidden by hardware compatibility/common sense
        #    (These are simple, so hand-crafted rules)
        # 3. If any constraints are unmet, optimize according budget/performance/multitasking importance
        self._create_tables()

        self._apply_rules()

        self._confirm_constraints()

        reuse_logger.debug('Done checking constraints and optimizing.')

        return self.cur_symbolic_soln

    def _create_tables(self):
        # This function should only depend on static things and user preferences, i.e., not read
        # or modify the current solution
        cpu_table=self.mappers[MAP_CPU].data
        gpu_table=self.mappers[MAP_GPU].data
        ram_table=self.mappers[MAP_RAM].data
        ssd_table=self.mappers[MAP_SSD].data
        hdd_table=self.mappers[MAP_HDD].data
        opt_table=self.mappers[MAP_OPT].data

        if self.user_request.constraints.cpu_brand in ['Intel', 'AMD']:
            self.cpu_table = cpu_table[cpu_table['Manufacturer']==self.user_request.constraints.cpu_brand]
            self.cpu_table_alt = None
        elif self.user_request.constraints.cpu_brand in ['PreferIntel', 'PreferAMD']:
            brand = 'Intel' if self.user_request.constraints.cpu_brand == 'PreferIntel' else 'AMD'
            self.cpu_table = cpu_table[cpu_table['Manufacturer']==brand]
            self.cpu_table_alt = cpu_table
        else:
            self.cpu_table = cpu_table
            self.cpu_table_alt = None

        if self.user_request.constraints.gpu_brand in ['NVIDIA', 'AMD']:
            self.gpu_table = gpu_table[gpu_table['Manufacturer']==self.user_request.constraints.gpu_brand]
            self.gpu_table_alt = None
        elif self.user_request.constraints.gpu_brand in ['PreferNVIDIA', 'PreferAMD']:
            brand = 'NVIDIA' if self.user_request.constraints.gpu_brand == 'PreferNVIDIA' else 'AMD'
            self.gpu_table = gpu_table[gpu_table['Manufacturer']==brand]
            self.gpu_table_alt = gpu_table
        else:
            self.gpu_table = gpu_table
            self.gpu_table_alt = None

        if self.user_request.constraints.min_ram is not None:
            # Capacity needs to be scaled to match the RAM table units
            reuse_logger.debug(self.user_request.constraints.min_ram)
            ram_limit=np.log2(np.array(self.user_request.constraints.min_ram)+1)
            ram_limit=self.mappers[MAP_RAM].scaler['scaler'].transform(ram_limit.reshape(-1,1))[0,0]
            reuse_logger.debug(ram_limit)
            self.ram_table=ram_table[ram_table['Capacity']>=ram_limit]
        else:
            self.ram_table = ram_table

        self.ssd_table = ssd_table
        self.hdd_table = hdd_table
        self.opt_drive_table = opt_table

        # Now, attempt to extract the priority order in which rules should be applied based on preferences
        reuse_logger.debug(self.user_request.raw_preferences)
        prefs=self.user_request.raw_preferences
        voter=defaultdict(lambda : 0)
        voter['Budget'] += prefs[0]*4+1
        if prefs[3] >= 0.5:
            # GPU More important for performance if Gaming is important
            voter['CPU'] += (prefs[1]*4+1)/2
            voter['GPU'] += prefs[1]*4+1
        else:
            # Otherwise CPU more important
            voter['CPU'] += prefs[1]*4+1
            voter['GPU'] += (prefs[1]*4+1)/2
        # Make RAM as important as Multitasking/Production
        voter['RAM'] += ((prefs[2]*4+1)+(prefs[5]*4+1))/2
        voter['SSD'] += prefs[6]*4+1
        voter['HDD'] += (1-prefs[6])*4+1
        # Tie-breaker for SSD/HDD based on perf vs. budget
        if prefs[1] > prefs[0]:
            voter['SSD'] += 1
        else:
            voter['HDD'] += 1
        voter = sorted(voter, key=lambda x: x[1],reverse=True)
        self.priorities=voter

    def _apply_rules(self):
        # This function should modify the current solution and apply various rules in order to
        # make it acceptable. Unfortunately, we need to sync the symbolic and numeric versions of
        # the solution after each rule application, but its a very small structure, so it's cheap.

        reuse_logger.debug('applying rules...')

        # TODO: Remove this block after rules are done. Insert temporary code to force various rules to fire here.

        # There are two passes through the priorities:
        # First pass: Goes in priority order and is simply to address some fundamental storage requirements.
        # Second pass: Goes in reverse-priority order, which allows the more important rules to be processed
        #              last and override less-important items.

        # First pass: in priority order
        for pri in self.priorities:
            if pri == 'CPU':
                pass
            elif pri == 'GPU':
                pass
            elif pri == 'RAM':
                # We will just rely on the filtered RAM table to tell us what's valid
                # Commented code provided here to perform symbolic->numeric translation if you need
                # a more explicit comparison
                #min_ram = self.user_request.constraints.min_ram
                #min_ram_norm = np.log2( np.array(min_ram) + 1 )
                #min_ram_norm = self.mappers[MAP_RAM].scaler['scaler'].transform(min_ram_norm.reshape(-1,1))[0][0]
                if self.cur_numeric_soln[MAP_RAM] < self.ram_table['Capacity'].iloc[0]:
                    self.cur_symbolic_soln[MAP_RAM] = self.user_request.constraints.min_ram
            elif pri == 'SSD':
                # Note: This rule is tied to the HDD rule. Whichever one has higher priority will fire first,
                #       giving a small SSD if this fires first
                if self.cur_symbolic_soln[MAP_SSD] + self.cur_symbolic_soln[MAP_HDD] == 0:
                    # Pick the first one that's non-zero
                    desired_ssd_size = self.ssd_table['Capacity'].iloc[1]
                    desired_ssd_size=self.mappers[MAP_SSD].scaler['scaler'].inverse_transform(np.array(desired_ssd_size).reshape(-1,1))[0,0]
                    desired_ssd_size = np.power(2, desired_ssd_size) - 1
                    self.cur_symbolic_soln[MAP_SSD] = desired_ssd_size
            elif pri == 'HDD':
                # Note: This rule is tied to the SSD rule. Whichever one has higher priority will fire first,
                #       giving a small HDD if this fires first
                if self.cur_symbolic_soln[MAP_SSD] + self.cur_symbolic_soln[MAP_HDD] == 0:
                    # Pick the first one that's non-zero
                    desired_hdd_size = self.hdd_table['Capacity'].iloc[1]
                    desired_hdd_size=self.mappers[MAP_HDD].scaler['scaler'].inverse_transform(np.array(desired_hdd_size).reshape(-1,1))[0,0]
                    desired_hdd_size = np.power(2, desired_hdd_size) - 1
                    self.cur_symbolic_soln[MAP_HDD] = desired_hdd_size
            elif pri == 'Budget':
                pass

            # Sync numeric and symbolic solutions each pass
            self._sync_numeric_symbolic()

        # Second pass: in reverse-priority order
        for pri in self.priorities[::-1]:
            if pri == 'CPU':
                # If the current CPU isn't on the preferred list (created by constraints), try to pick one that is
                if not any(self.cpu_table['CPU Name']==self.cur_symbolic_soln[MAP_CPU]):
                    cpu_found = False
                    reuse_logger.debug('CPU not on preferred list. Replacing...')
                    candidate_cpus = self.cpu_table[self.cpu_table['CPU Mark'] >= self.cur_numeric_soln[MAP_CPU]]
                    if not candidate_cpus.empty:
                        cheapest = candidate_cpus['MSRP'].idxmin()
                        # Update it with cheapest equivalent/better CPU
                        self.cur_symbolic_soln[MAP_CPU] = candidate_cpus.loc[cheapest]['CPU Name']
                        cpu_found = True

                    # Check alternate list, if required
                    if not cpu_found:
                        reuse_logger.debug('No suitable CPU in preferred list. Checking alternate list...')
                        candidate_cpus = self.cpu_table_alt[self.cpu_table_alt['CPU Mark'] >= self.cur_numeric_soln[MAP_CPU]]
                        if not candidate_cpus.empty:
                            cheapest = candidate_cpus['MSRP'].idxmin()
                            # Update it with cheapest equivalent/better CPU
                            self.cur_symbolic_soln[MAP_CPU] = candidate_cpus.loc[cheapest]['CPU Name']
            elif pri == 'GPU':
                print(self.gpu_table)
                print(self.mappers[MAP_GPU].data)
                # If the current GPU isn't on the preferred list (created by constraints), try to pick one that is
                if not any(self.gpu_table['GPU Name']==self.cur_symbolic_soln[MAP_GPU]):
                    gpu_found = False
                    reuse_logger.debug('GPU not on preferred list. Replacing...')
                    candidate_gpus = self.gpu_table[self.gpu_table['Benchmark'] >= self.cur_numeric_soln[MAP_GPU]]
                    if not candidate_gpus.empty:
                        cheapest = candidate_gpus['MSRP'].idxmin()
                        # Update it with cheapest equivalent/better GPU
                        self.cur_symbolic_soln[MAP_GPU] = candidate_gpus.loc[cheapest]['GPU Name']
                        gpu_found = True

                    # Check alternate list, if required
                    if not gpu_found:
                        reuse_logger.debug('No suitable GPU in preferred list. Checking alternate list...')
                        candidate_gpus = self.gpu_table_alt[self.gpu_table_alt['Benchmark'] >= self.cur_numeric_soln[MAP_GPU]]
                        if not candidate_gpus.empty:
                            cheapest = candidate_gpus['MSRP'].idxmin()
                            # Update it with cheapest equivalent/better GPU
                            self.cur_symbolic_soln[MAP_GPU] = candidate_gpus.loc[cheapest]['GPU Name']
                pass
            elif pri == 'RAM':
                pass
            elif pri == 'SSD':
                pass
            elif pri == 'HDD':
                pass
            elif pri == 'Budget':
                pass

            # Sync numeric and symbolic solutions each pass
            self._sync_numeric_symbolic()

        return

    def _sync_numeric_symbolic(self):
        # IMPORTANT Note: This function assumes the input is in the symbolic solution and will replace the
        #                 numeric one. It has to do an extra copy back to numeric because the price is
        #                 updated on the numeric->symbolic conversion.
        additional_info=[]
        self.cur_numeric_soln = self._map_to_numeric(self.cur_symbolic_soln, additional_info=additional_info)
        self.cur_addl_info = additional_info
        self.cur_symbolic_soln = self._map_to_closest(self.cur_numeric_soln)
        additional_info=[]
        self.cur_numeric_soln = self._map_to_numeric(self.cur_symbolic_soln, additional_info=additional_info)
        self.cur_addl_info = additional_info

    def _confirm_constraints(self):
        reuse_logger.debug('confiriming constraints...')
        # If any constraints are unmet, customize the solution to meet them, taking the relative
        # importance user preferences into account (likely just budget/performance/multitasking now)

        # if CPU==AMD GPU can't be Integrated

        return

    def _check_optimizations(self, solution):
        reuse_logger.debug('checking for additional optimizations...')
        return

    def _map_to_closest(self, adapted_solution):
            # Mapping to closest real component.
            # Putting into a function since it's a human-readable way to monitor
            # transformations as they are applied. This function is also important
            # because it calculates the price of the assembled solution.

            # Copy data so we don't destroy it
            tmp_adapted_solution = adapted_solution.copy()

            # TODO: Should we start the price off higher than 0 to account for miscellaneous things
            #       like motherboard, case, etc., and be a fairer comparison to the cases? I'm
            #       thinking maybe 150-200€ might be a fair starting point...
            solution_price = 0
            for idx in range(len(tmp_adapted_solution) - 1):
                tmp_adapted_solution[idx] = self.mappers[idx].transform(np.array(tmp_adapted_solution[idx]),
                                                            from_col=self.mappers[idx].scaler_columns[0],
                                                            to_col=target_columns[idx])[0]
                solution_price += self.mappers[idx].transform(np.array(tmp_adapted_solution[idx]),
                                                         from_col=target_columns[idx],
                                                         to_col=price_columns[idx],
                )[0]
            tmp_adapted_solution[-1] = np.round(solution_price, 2)

            # Transformation of Log2 components.
            for idx in range(1, 4):
                tmp_adapted_solution[idx] = np.round(
                    np.power(
                        2,
                        self.scalers[idx-1].inverse_transform(
                            [[tmp_adapted_solution[idx]]])[0][0]
                    ) - 1
                )
            return tmp_adapted_solution

    def _map_to_numeric(self, symbolic, additional_info=None):
            # Copy data so we don't destroy it
            numeric = symbolic.copy()

            # If additional info is requested, set up the structure
            if additional_info == []:
                cpu_brand=self.mappers[MAP_CPU].transform(np.array(numeric[MAP_CPU]),
                                                from_col=target_columns[MAP_CPU],
                                                to_col='Manufacturer')[0]
                gpu_brand=self.mappers[MAP_GPU].transform(np.array(numeric[MAP_GPU]),
                                                from_col=target_columns[MAP_GPU],
                                                to_col='Manufacturer')[0]
                additional_info.append(cpu_brand)
                additional_info.append(gpu_brand)

            # Convert symbolic things (CPU/GPU names) to numbers
            numeric[MAP_CPU]=self.mappers[MAP_CPU].transform(np.array(numeric[MAP_CPU]),
                                                             from_col=target_columns[MAP_CPU],
                                                             to_col=self.mappers[MAP_CPU].scaler_columns[0])[0]
            numeric[MAP_GPU]=self.mappers[MAP_GPU].transform(np.array(numeric[MAP_GPU]),
                                                             from_col=target_columns[MAP_GPU],
                                                             to_col=self.mappers[MAP_GPU].scaler_columns[0])[0]

            # Transformation of Log2 components.
            numeric[1:4] = np.log2(np.array(numeric[1:4])+1)

            for i in range(1,4):
                numeric[i]=self.mappers[i].scaler['scaler'].transform(np.array(numeric[i]).reshape(-1,1))
                numeric[i]=self.mappers[i].transform(numeric[i],
                                                from_col=target_columns[i],
                                                to_col=self.mappers[i].scaler_columns[0])[0]

            # Note: No transformations required for optical drive or price

            return numeric

    def _get_cpu_brand(self, cpu):
        cpu_table = self.mappers[MAP_CPU].data
        entry = cpu_table[cpu_table['CPU Name']==cpu]
        brand = entry['Manufacturer'].iloc[0]
        return brand

    def _get_gpu_brand(self, gpu):
        gpu_table = self.mappers[MAP_GPU].data
        entry = gpu_table[gpu_table['GPU Name']==gpu]
        brand = entry['Manufacturer'].iloc[0]
        return brand
