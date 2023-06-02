import os
import argparse
import csv
import shutil
import copy
from typing import Any

import jinja2

from xml.etree import ElementTree

from flamapy.metamodels.configuration_metamodel.models import Configuration
from flamapy.metamodels.pysat_metamodel.transformations import FmToPysat
from flamapy.metamodels.pysat_metamodel.operations import Glucose3Products

from flamapy.metamodels.fm_metamodel.models import FeatureModel, Feature
from flamapy.metamodels.fm_metamodel.transformations import UVLReader

from variation_point import VariationPoint, Variant


# CONSTANTS
FM = 'feature_models/LogicFormula.uvl'


def get_files(dir: str) -> tuple[list[str], list[str]]:
    """Return the list of configurations files (.xml) and 
    the list of attributes files (.csv) in the provided directory.
    """
    configurations_files = []
    attributes_files = []
    for root, dirs, files in os.walk(dir):
        for file in files:
            filepath = os.path.join(root, file)
            _, file_extension = os.path.splitext(filepath)
            if file_extension.endswith('xml'):
                configurations_files.append(filepath)
            elif file_extension.endswith('csv'):
                attributes_files.append(filepath)
    configurations_files.sort()
    attributes_files.sort()
    print(configurations_files)
    return (configurations_files, attributes_files)


def load_feature_model() -> FeatureModel:
    """Load the feature model."""
    return UVLReader(FM).transform()


def get_feature_from_fm(feature_name: str, fm: FeatureModel) -> Feature:
    """Return the feature object from the feature model."""
    feature = fm.get_feature_by_name(feature_name)
    print(f'Feature: {feature_name}')
    if feature is not None:
        return feature
    raise Exception(f'The feature {feature_name} does not exist in the feature model.')


def parse_configuration(filepath: str, fm: FeatureModel) -> Configuration:
    """Parse a .xml configuration file generated with FeatureIDE."""
    tree = ElementTree.parse(filepath)
    root = tree.getroot()

    features = {}
    for child in root:
        if child.attrib:
            feature_name = child.attrib['name']
            if 'automatic' in child.attrib:
                feature_selected = child.attrib['automatic'] == 'selected'
            elif 'manual' in child.attrib:
                feature_selected = child.attrib['manual'] == 'selected'
            else:
                feature_selected = False
            feature_object = get_feature_from_fm(feature_name, fm)
            features[feature_object] = feature_selected
    return Configuration(elements=features)


def parse_attributes(filepath: str) -> dict[str, str]:
    """Parse a .csv file with the attributes configured."""
    attributes = {}  # dictionary of attribute_identifier -> value
    with open(filepath, mode='r') as file:
        csv_reader = csv.DictReader(file, skipinitialspace=True)
        for row in csv_reader:
            attribute = row['Attribute']
            value = row['Value']
            attributes[attribute] = value
    return attributes


def load_mapping_model(filepath: str, fm: FeatureModel) -> dict[str, VariationPoint]:
    """Load the mapping model with the variation points and variants information."""
    variation_points = {}  # dictionary of feature -> variation points
    with open(filepath, mode='r') as file:
        csv_reader = csv.DictReader(file, skipinitialspace=True)
        for row in csv_reader:
            vp_feature = row['VariationPointFeature']
            vp_handler = row['Handler']
            variant_feature = row['VariantIdentifier']
            variant_value = row['VariantValue']
            if '.' in variant_feature:  # it is an attribute
                key = variant_feature
                variation_points[variant_feature] = VariationPoint(feature=get_feature_from_fm(vp_feature, fm), handler=vp_handler)
            elif not vp_feature in variation_points:
                key = vp_feature
                variation_points[vp_feature] = VariationPoint(feature=get_feature_from_fm(vp_feature, fm), handler=vp_handler)
            else:
                key = vp_feature
            if variant_value == '-':
                variant_value = None
            variant = Variant(identifier=variant_feature, value=variant_value)
            variation_points[key].variants.append(variant)
    return variation_points


def mapping_model_by_configurations(mapping_model: dict[str, VariationPoint], configurations: list[Configuration]) -> list[dict[str, VariationPoint]]:
    map_models = []
    for i, config in enumerate(configurations):
        rec_features = [f for f in config.get_selected_elements() if any(a.name == 'rec' for a in f.attributes)]
        new_mapping_model = copy.deepcopy(mapping_model)
        for k, v in new_mapping_model.items():
            if v.handler == 'Expr':
                v.handler = f'{v.handler}{i-1}' if i != 0 else v.handler
                for variant in v.variants:
                    variant.value = variant.value.replace('Expr', f'Expr{i}')
        map_models.append(new_mapping_model)
    return map_models


def is_selected_in_a_configuration(feature: Feature, configurations: list[Configuration]) -> bool:
    return any(feature in config.elements and config.elements[feature] for config in configurations)


def get_attribute_value(identifier: str, attributes: list[dict[str, str]]) -> str:
    for attributes_dict in attributes:
        if identifier in attributes_dict:
            return attributes_dict[identifier]
    return None


def get_variant_value(fm: FeatureModel, variation_point: VariationPoint, configurations: list[Configuration], attributes: list[dict[str, str]]) -> str:
    """Return the value of the variant according to the provided configurations/attributes."""
    for variant in variation_point.variants:
        identifier = variant.identifier
        if '.' in identifier:
            feature = identifier[:identifier.index('.')]
            value = get_attribute_value(identifier, attributes)
        else:
            feature = identifier
            value = variant.value
        if is_selected_in_a_configuration(get_feature_from_fm(feature, fm), configurations):
            return value
    return None
        
def get_variant_value_in_configuration(fm: FeatureModel, variation_point: VariationPoint, configuration: Configuration, attributes: dict[str, str]) -> str:
    """Return the value of the variant according to a specific configuration/attributes."""
    for variant in variation_point.variants:
        identifier = variant.identifier
        if '.' in identifier:
            feature = identifier[:identifier.index('.')]
            value = attributes.get(identifier)
        else:
            feature = identifier
            value = variant.value
        feature = get_feature_from_fm(feature, fm)
        if feature in configuration.elements and configuration.elements[feature]:
            return value
    return None
        

def build_template_maps(fm: FeatureModel, mapping_model: dict[str, VariationPoint], configurations: list[Configuration], attributes: list[dict[str, str]]) -> dict[str, Any]:
    #set_of_attributes = {a[a.index('.')+1:] for a_dict in attributes for a in a_dict.keys()}
    maps = {}
    multi_features_maps = []

    # Simple features
    for vp in mapping_model.values():
        if not '.' in vp.handler:  # it is a simple feature (not a multi-feature)
            if is_selected_in_a_configuration(vp.feature, configurations):
                if not vp.variants:
                    maps[vp.handler] = True
                elif vp.variants[0].identifier == '-':
                    maps[vp.handler] = True
                else:
                    maps[vp.handler] = get_variant_value(fm, vp, configurations, attributes)
    return maps


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate Logical Formula')
    parser.add_argument('-f', dest='folder', type=str, required=True, 
                        help='Directory with the configurations and attributes files.')
    args = parser.parse_args()

    # Identify configurations and attributes files
    configurations_files, attributes_files = get_files(args.folder)

    print('CONFIGURATION FILES:')
    for i, config_file in enumerate(configurations_files):
        print(f'|-{i}: {config_file}')
    
    print('ATTRIBUTES FILES:')
    for i, attribute_file in enumerate(attributes_files):
        print(f'|-{i}: {attribute_file}')

    # Load the feature models
    fm = load_feature_model()

    sat_model = FmToPysat(fm).transform()
    products = Glucose3Products().execute(sat_model).get_result()
    print('PRODUCTS:')
    for i, p in enumerate(products):
        print(f'{i}: {[f for f in p if not fm.get_feature_by_name(f).is_abstract]}')


    # Parse configurations and attributes
    configurations = [parse_configuration(file, fm) for file in configurations_files]
    attributes = [parse_attributes(file) for file in attributes_files]

    # Load the mapping model
    mapping_model = load_mapping_model('mapping_models/mapping_model.csv', fm)
    print(f'MAPPING MODEL:')
    for i, vp in enumerate(mapping_model.values()):
        print(f'|-vp{i}: {vp}')
    map_models = mapping_model_by_configurations(mapping_model, configurations)
    print(f'MAP MODELS:')
    for mm in map_models:
        for i, vp in enumerate(mm.values()):
            print(f'|-vp{i}: {vp}')

    shutil.copyfile('templates/template.txt', 'formula.txt')

    for configuration, map_model in zip(configurations, map_models):
        print(f'Configuration: {[c.name for c in configuration.get_selected_elements()]}')
        maps = build_template_maps(fm, map_model, [configuration], attributes)
        print(f'TEMPLATE CONFIGURATION:')
        for h, v in maps.items():
            if isinstance(v, list):
                for i, multi_map in enumerate(v):
                    print(f'|-plot{i}: {multi_map}')
            else:
                print(f'|-{h}: {v}')

        template_loader = jinja2.FileSystemLoader(searchpath="./")
        environment = jinja2.Environment(loader=template_loader)
        template = environment.get_template('formula.txt')
        content = template.render(maps)

        with open('formula.txt', 'w', encoding='utf-8') as file:
            file.write(content)

    # maps = {'LExpr': 'a', 'RExpr': 'b'}
    # template_loader = jinja2.FileSystemLoader(searchpath="./")
    # environment = jinja2.Environment(loader=template_loader)
    # template = environment.get_template('formula.txt')
    # content = template.render(maps)

    # with open('formula.txt', 'w', encoding='utf-8') as file:
    #     file.write(content)

    # # Load the mapping model
    # mapping_model = load_mapping_model('mapping_models/pgfplots_map.csv', fms)
    # print(f'MAPPING MODEL:')
    # for i, vp in enumerate(mapping_model.values()):
    #     print(f'|-vp{i}: {vp}')

    # maps = build_template_maps(fms, mapping_model, configurations, attributes)
    # print(f'TEMPLATE CONFIGURATION:')
    # for h, v in maps.items():
    #     if isinstance(v, list):
    #         for i, multi_map in enumerate(v):
    #             print(f'|-plot{i}: {multi_map}')
    #     else:
    #         print(f'|-{h}: {v}')

    # template_loader = jinja2.FileSystemLoader(searchpath="./")
    # environment = jinja2.Environment(loader=template_loader)
    # template = environment.get_template('templates/template.tex')
    # content = template.render(maps)

    # with open('visualization.tex', 'w', encoding='utf-8') as file:
    #     file.write(content)

    #print(f'MAPPING MODEL: {mapping_model}')
    #print(f'CONFIGURATIONS: {configurations}')
    #print(f'ATTRIBUTES: {attributes}')
    