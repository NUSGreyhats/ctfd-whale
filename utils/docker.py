import inspect
import json
import random
import uuid
from collections import OrderedDict

import docker
from flask import current_app

from CTFd.utils import get_config

from .cache import CacheProvider
from .exceptions import WhaleError


def get_docker_client():
    if get_config("whale:docker_use_ssl", False):
        tls_config = docker.tls.TLSConfig(
            verify=True,
            ca_cert=get_config("whale:docker_ssl_ca_cert") or None,
            client_cert=(
                get_config("whale:docker_ssl_client_cert"),
                get_config("whale:docker_ssl_client_key")
            ),
        )
        return docker.DockerClient(
            base_url=get_config("whale:docker_api_url"),
            tls=tls_config,
            version="auto",
        )
    else:
        return docker.DockerClient(
            base_url=get_config("whale:docker_api_url"),
            version="auto",
        )


class DockerUtils:
    @staticmethod
    def _config_bool(key, default=False):
        val = get_config(key, default)
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ('1', 'true', 'yes', 'on')

    @staticmethod
    def _csv_config(key, default=''):
        val = get_config(key, default) or ''
        return [item.strip() for item in str(val).split(',') if item.strip()]

    @staticmethod
    def _key_value_config(key, default=''):
        options = {}
        for item in DockerUtils._csv_config(key, default):
            if '=' in item:
                k, v = item.split('=', 1)
                options[k.strip()] = v.strip()
        return options

    @staticmethod
    def _challenge_extra_networks(challenge):
        raw = getattr(challenge, 'extra_networks', '') or ''
        if isinstance(raw, (list, tuple)):
            networks = raw
        else:
            raw = str(raw).strip()
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
                networks = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                networks = raw.replace('\n', ',').split(',')

        return [str(network).strip() for network in networks if str(network).strip()]

    @staticmethod
    def _standalone_networks(challenge):
        networks = [get_config("whale:docker_auto_connect_network", "ctfd_frp-containers")]
        for network in DockerUtils._challenge_extra_networks(challenge):
            if network not in networks:
                networks.append(network)
        return networks

    @staticmethod
    def service_hardening_kwargs():
        """Security/resource defaults for challenge services.

        The docker SDK changed ContainerSpec kwargs over time, so inspect the
        installed SDK and only pass options it supports.
        """
        kwargs = {}
        try:
            spec_params = inspect.signature(docker.types.ContainerSpec.__init__).parameters
        except Exception:
            spec_params = {}

        if 'init' in spec_params and DockerUtils._config_bool('whale:docker_enable_init', True):
            kwargs['init'] = True

        if 'read_only' in spec_params and DockerUtils._config_bool('whale:docker_read_only', False):
            kwargs['read_only'] = True

        user = get_config('whale:docker_user', '')
        if 'user' in spec_params and user:
            kwargs['user'] = user

        cap_drop = DockerUtils._csv_config('whale:docker_cap_drop', 'NET_RAW')
        if 'cap_drop' in spec_params and cap_drop:
            kwargs['cap_drop'] = cap_drop

        log_driver = get_config('whale:docker_log_driver', 'json-file')
        if log_driver:
            kwargs['log_driver'] = log_driver
            log_options = DockerUtils._key_value_config(
                'whale:docker_log_options', 'max-size=10m,max-file=3'
            )
            if log_options:
                kwargs['log_driver_options'] = log_options

        restart_condition = get_config('whale:docker_restart_condition', 'none')
        if restart_condition:
            kwargs['restart_policy'] = docker.types.RestartPolicy(condition=restart_condition)

        return kwargs

    @staticmethod
    def init():
        try:
            DockerUtils.client = get_docker_client()
            # docker-py is thread safe: https://github.com/docker/docker-py/issues/619
        except Exception:
            raise WhaleError(
                'Docker Connection Error\n'
                'Please ensure the docker api url (first config item) is correct\n'
                'if you are using unix:///var/run/docker.sock, check if the socket is correctly mapped'
            )
        credentials = get_config("whale:docker_credentials")
        if credentials and credentials.count(':') == 1:
            try:
                DockerUtils.client.login(*credentials.split(':'))
            except Exception:
                raise WhaleError('docker.io failed to login, check your credentials')

    @staticmethod
    def add_container(container):
        if container.challenge.docker_image.startswith("{"):
            DockerUtils._create_grouped_container(container)
        else:
            DockerUtils._create_standalone_container(container)

    @staticmethod
    def _create_standalone_container(container):
        dns = DockerUtils._csv_config("whale:docker_dns", "")
        node = DockerUtils.choose_node(
            container.challenge.docker_image,
            get_config("whale:docker_swarm_nodes", "").split(",")
        )

        DockerUtils.client.services.create(
            image=container.challenge.docker_image,
            name=f'{container.user_id}-{container.uuid}',
            env={'FLAG': container.flag}, dns_config=docker.types.DNSConfig(nameservers=dns),
            networks=DockerUtils._standalone_networks(container.challenge),
            resources=docker.types.Resources(
                mem_limit=DockerUtils.convert_readable_text(
                    container.challenge.memory_limit),
                cpu_limit=int(container.challenge.cpu_limit * 1e9)
            ),
            labels={
                'whale_id': f'{container.user_id}-{container.uuid}'
            },  # for container deletion
            constraints=['node.labels.name==' + node],
            endpoint_spec=docker.types.EndpointSpec(mode='dnsrr', ports={}),
            **DockerUtils.service_hardening_kwargs()
        )

    @staticmethod
    def _create_grouped_container(container):
        cache = CacheProvider(app=current_app)
        range_prefix = cache.get_available_network_range()

        ipam_pool = docker.types.IPAMPool(subnet=range_prefix)
        ipam_config = docker.types.IPAMConfig(
            driver='default', pool_configs=[ipam_pool])
        network_name = f'{container.user_id}-{container.uuid}'
        network = DockerUtils.client.networks.create(
            network_name, internal=True,
            ipam=ipam_config, attachable=True,
            labels={'prefix': range_prefix},
            driver="overlay", scope="swarm"
        )

        dns = []
        containers = get_config("whale:docker_auto_connect_containers", "").split(",")
        for c in containers:
            if not c:
                continue
            network.connect(c)
            if "dns" in c:
                network.reload()
                for name in network.attrs['Containers']:
                    if network.attrs['Containers'][name]['Name'] == c:
                        dns.append(network.attrs['Containers'][name]['IPv4Address'].split('/')[0])

        has_processed_main = False
        try:
            try:
                images = json.loads(
                    container.challenge.docker_image,
                    object_pairs_hook=OrderedDict
                )
            except json.JSONDecodeError:
                raise WhaleError(
                    "Challenge Image Parse Error\n"
                    "plase check the challenge image string"
                )
            for name, image in images.items():
                if has_processed_main:
                    container_name = f'{container.user_id}-{uuid.uuid4()}'
                else:
                    container_name = f'{container.user_id}-{container.uuid}'
                    node = DockerUtils.choose_node(image, get_config("whale:docker_swarm_nodes", "").split(","))
                    has_processed_main = True
                DockerUtils.client.services.create(
                    image=image, name=container_name, networks=[
                        docker.types.NetworkAttachmentConfig(network_name, aliases=[name])
                    ],
                    env={'FLAG': container.flag},
                    dns_config=docker.types.DNSConfig(nameservers=dns),
                    resources=docker.types.Resources(
                        mem_limit=DockerUtils.convert_readable_text(
                            container.challenge.memory_limit
                        ),
                        cpu_limit=int(container.challenge.cpu_limit * 1e9)),
                    labels={
                        'whale_id': f'{container.user_id}-{container.uuid}'
                    },  # for container deletion
                    hostname=name, constraints=['node.labels.name==' + node],
                    endpoint_spec=docker.types.EndpointSpec(mode='dnsrr', ports={}),
                    **DockerUtils.service_hardening_kwargs()
                )
        except Exception:
            whale_id = f'{container.user_id}-{container.uuid}'
            for s in DockerUtils.client.services.list(filters={'label': f'whale_id={whale_id}'}):
                s.remove()
            auto_containers = get_config("whale:docker_auto_connect_containers", "").split(",")
            for c in auto_containers:
                try:
                    network.disconnect(c, force=True)
                except Exception:
                    pass
            network.remove()
            cache.add_available_network_range(range_prefix)
            raise

    @staticmethod
    def remove_container(container):
        whale_id = f'{container.user_id}-{container.uuid}'

        for s in DockerUtils.client.services.list(filters={'label': f'whale_id={whale_id}'}):
            s.remove()

        networks = DockerUtils.client.networks.list(names=[whale_id])
        if len(networks) > 0:  # is grouped containers
            auto_containers = get_config("whale:docker_auto_connect_containers", "").split(",")
            redis_util = CacheProvider(app=current_app)
            for network in networks:
                for container in auto_containers:
                    try:
                        network.disconnect(container, force=True)
                    except Exception:
                        pass
                network.remove()
                redis_util.add_available_network_range(network.attrs['Labels']['prefix'])

    @staticmethod
    def convert_readable_text(text):
        lower_text = text.lower()

        if lower_text.endswith("k"):
            return int(text[:-1]) * 1024

        if lower_text.endswith("m"):
            return int(text[:-1]) * 1024 * 1024

        if lower_text.endswith("g"):
            return int(text[:-1]) * 1024 * 1024 * 1024

        return int(text)

    @staticmethod
    def choose_node(image, nodes):
        win_nodes = []
        linux_nodes = []
        for node in nodes:
            if node.startswith("windows"):
                win_nodes.append(node)
            else:
                linux_nodes.append(node)
        try:
            tag = image.split(":")[1:]
            if len(tag) and tag[0].startswith("windows"):
                return random.choice(win_nodes)
            return random.choice(linux_nodes)
        except IndexError:
            raise WhaleError(
                'No Suitable Nodes.\n'
                'If you are using Whale for the first time, \n'
                'Please Setup Swarm Nodes Correctly and Lable Them with\n'
                'docker node update --label-add "name=linux-1" $(docker node ls -q)'
            )
