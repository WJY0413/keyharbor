"""Synthetic registry: never read the user's profiles or credentials."""
def registry():
    return {'schema':1, 'port':4103, 'keys':[
        {'id':name, 'prefix':name+'-', 'secret_file':name+'.dpapi',
         'api_base':'https://upstream.example/v1', 'enabled':True,
         'visible':[], 'fallback_keys':[], 'aliases':{}, 'modes':{'test':'responses'}}
        for name in ('alpha','beta')
    ]}
